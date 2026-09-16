#!/usr/bin/env bash
#
# Banc d'essai en boucle pour CloneGator.
#
# Fabrique un parc de disques virtuels réaliste — vrais systèmes de fichiers,
# vrais fichiers dedans — sur lequel le moteur tourne exactement comme sur du
# matériel, mais en quelques secondes au lieu d'une dizaine de minutes.
#
# Le parc contient délibérément les cas qui échouent : une cible trop petite,
# une cible portant une table périmée, une cible plus grande que la source, et
# une source dont les numéros de partition ne sont pas contigus. Ce sont les
# situations où clonesrv échouait mal. Un banc qui ne les contient pas ne
# prouve rien.
#
#   sudo ./outils/banc.sh creer
#   sudo ./outils/banc.sh etat
#   sudo ./outils/banc.sh detruire
#
set -euo pipefail

BANC="${CLONEGATOR_BANC:-/var/tmp/clonegator-banc}"
MONTAGE="$BANC/.montage"

rouge()  { printf '\033[31m%s\033[0m\n' "$*"; }
vert()   { printf '\033[32m%s\033[0m\n' "$*"; }
titre()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

exiger_root() {
    if [ "$(id -u)" -ne 0 ]; then
        rouge "Ce script a besoin des droits root (losetup, mkfs, mount)."
        exit 1
    fi
}

exiger_outils() {
    local manquants=()
    local outil
    for outil in losetup sgdisk sfdisk partprobe mkfs.ext4 mkfs.vfat; do
        command -v "$outil" >/dev/null 2>&1 || manquants+=("$outil")
    done
    if [ "${#manquants[@]}" -ne 0 ]; then
        rouge "Outils manquants : ${manquants[*]}"
        echo "  apt-get install -y util-linux gdisk parted e2fsprogs dosfstools"
        exit 1
    fi
}

# Renvoie le périphérique loop associé à une image, vide si aucun.
# `losetup -j` sort « /dev/loop0: 0 /chemin/image » : on garde le premier champ.
boucle_de() {
    losetup -j "$1" 2>/dev/null | head -n1 | cut -d: -f1
}

attacher() {
    local image="$1"
    local boucle
    boucle="$(boucle_de "$image")"
    if [ -z "$boucle" ]; then
        boucle="$(losetup --find --show --partscan "$image")"
    fi
    echo "$boucle"
}

reglages() {
    partprobe "$1" >/dev/null 2>&1 || true
    udevadm settle >/dev/null 2>&1 || true
}

creer_image() {
    local nom="$1" taille="$2"
    truncate -s "$taille" "$BANC/$nom.img"
    attacher "$BANC/$nom.img"
}

# ---------------------------------------------------------------- sources ---

creer_source_gpt() {
    titre "source-gpt — GPT, ESP + NTFS + ext4, avec des fichiers"
    local boucle
    boucle="$(creer_image source-gpt 3G)"

    sgdisk --clear \
        -n 1:2048:+256M -t 1:ef00 -c 1:"ESP" \
        -n 2:0:+1G      -t 2:0700 -c 2:"Windows" \
        -n 3:0:0        -t 3:8300 -c 3:"Donnees" \
        "$boucle" >/dev/null
    reglages "$boucle"

    mkfs.vfat -F 32 -n ESP "${boucle}p1" >/dev/null

    if command -v mkfs.ntfs >/dev/null 2>&1; then
        mkfs.ntfs -Q -F -L Windows "${boucle}p2" >/dev/null
        echo "  partition 2 : NTFS"
    else
        mkfs.ext4 -q -F -L Windows "${boucle}p2"
        echo "  partition 2 : ext4 (mkfs.ntfs absent — installe ntfs-3g)"
    fi

    mkfs.ext4 -q -F -L Donnees "${boucle}p3"

    mkdir -p "$MONTAGE"

    # L'ESP reçoit de quoi valider le contrôle de démarrage du §11.
    mount "${boucle}p1" "$MONTAGE"
    mkdir -p "$MONTAGE/EFI/Microsoft/Boot"
    printf 'faux chargeur de demarrage\n' > "$MONTAGE/EFI/Microsoft/Boot/bootmgfw.efi"
    umount "$MONTAGE"

    mount "${boucle}p3" "$MONTAGE"
    mkdir -p "$MONTAGE/Utilisateurs/eleve" "$MONTAGE/Programmes"
    head -c 40M /dev/urandom > "$MONTAGE/Programmes/gros.bin"
    head -c 3M  /dev/urandom > "$MONTAGE/Utilisateurs/eleve/devoir.bin"
    printf 'CloneGator banc d essai\n' > "$MONTAGE/Utilisateurs/eleve/note.txt"
    sync
    # Le manifeste sert de référence : après un clonage, on remonte la cible et
    # on relance le même sha256sum -c.
    ( cd "$MONTAGE" && find . -type f -print0 | sort -z \
        | xargs -0 sha256sum ) > "$BANC/source-gpt.sha256"
    umount "$MONTAGE"

    vert "  $boucle prêt — manifeste dans $BANC/source-gpt.sha256"
}

creer_source_trous() {
    titre "source-trous — GPT dont les partitions sont numérotées 1, 2, 3, 5"
    local boucle
    boucle="$(creer_image source-trous 2G)"

    # Le numéro 4 est volontairement absent. clonesrv bouclait de 1 à N et
    # abandonnait ici — après avoir déjà effacé toutes les cibles.
    sgdisk --clear \
        -n 1:2048:+128M -t 1:ef00 -c 1:"ESP" \
        -n 2:0:+256M    -t 2:0700 -c 2:"Reserve" \
        -n 3:0:+256M    -t 3:8300 -c 3:"Systeme" \
        -n 5:0:0        -t 5:8300 -c 5:"Recuperation" \
        "$boucle" >/dev/null
    reglages "$boucle"

    mkfs.vfat -F 32 "${boucle}p1" >/dev/null
    mkfs.ext4 -q -F "${boucle}p3"
    mkfs.ext4 -q -F "${boucle}p5"

    vert "  $boucle prêt — numéros : $(lsblk -nro NAME "$boucle" | tail -n +2 | tr '\n' ' ')"
}

# ----------------------------------------------------------------- cibles ---

creer_cible_egale() {
    titre "cible-egale — 3G, vierge, même taille que la source"
    vert "  $(creer_image cible-egale 3G) prêt"
}

creer_cible_grande() {
    titre "cible-grande — 4G, vierge, plus grande que la source"
    echo "  Vérifie le repositionnement de l'en-tête GPT de secours."
    vert "  $(creer_image cible-grande 4G) prêt"
}

creer_cible_petite() {
    titre "cible-petite — 2G, trop petite pour la source"
    echo "  Doit être écartée AVANT toute écriture sur les autres cibles."
    vert "  $(creer_image cible-petite 2G) prêt"
}

creer_cible_sale() {
    titre "cible-sale — 3G portant une table MBR périmée et un ext4"
    local boucle
    boucle="$(creer_image cible-sale 3G)"

    printf 'label: dos\nstart=2048, size=2000000, type=83\n' | sfdisk -q "$boucle" >/dev/null
    reglages "$boucle"
    mkfs.ext4 -q -F -L Perime "${boucle}p1"

    vert "  $boucle prêt"
}

# --------------------------------------------------------------- commandes ---

creer() {
    exiger_root
    exiger_outils
    mkdir -p "$BANC" "$MONTAGE"

    creer_source_gpt
    creer_source_trous
    creer_cible_egale
    creer_cible_grande
    creer_cible_petite
    creer_cible_sale

    etat
}

detruire() {
    exiger_root
    if [ ! -d "$BANC" ]; then
        echo "Rien à détruire : $BANC n'existe pas."
        return 0
    fi

    if mountpoint -q "$MONTAGE" 2>/dev/null; then
        umount "$MONTAGE"
    fi

    local image boucle
    for image in "$BANC"/*.img; do
        [ -e "$image" ] || continue
        boucle="$(boucle_de "$image")"
        if [ -n "$boucle" ]; then
            losetup -d "$boucle" || true
            echo "  détaché $boucle"
        fi
    done

    rm -rf "$BANC"
    vert "Banc détruit."
}

etat() {
    titre "État du banc — $BANC"
    if [ ! -d "$BANC" ]; then
        echo "  (aucun banc créé)"
        return 0
    fi

    local image boucle
    for image in "$BANC"/*.img; do
        [ -e "$image" ] || continue
        boucle="$(boucle_de "$image")"
        printf '  %-22s %-12s %s\n' \
            "$(basename "$image")" \
            "${boucle:-détaché}" \
            "$(du -h --apparent-size "$image" | cut -f1) apparent, $(du -h "$image" | cut -f1) réel"
    done

    echo
    echo "  Pour voir ce que CloneGator en comprend :"
    echo "    python3 -m clonegator disques"
}

case "${1:-}" in
    creer)    creer ;;
    detruire) detruire ;;
    etat)     etat ;;
    *)
        echo "usage : $0 {creer|detruire|etat}"
        echo
        echo "  creer     fabrique le parc de disques virtuels"
        echo "  etat      montre ce qui existe et où c'est attaché"
        echo "  detruire  détache les boucles et supprime tout"
        exit 1
        ;;
esac
