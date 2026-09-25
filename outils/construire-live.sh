#!/usr/bin/env bash
#
# Construit le CloneGator live dans dist/ (§15 de l'analyse, phase 6 du plan).
#
#   ./outils/construire-live.sh
#
# Un Debian 13 minimal, fabriqué par mmdebstrap, reçoit le paquet .deb de
# CloneGator et les fichiers de live/systeme/. On en tire :
#   - dist/clonegator-live_<version>.iso : ISO hybride, BIOS et UEFI (Secure
#     Boot par le shim et le GRUB signés de Debian), pour une clé ou un CD ;
#   - dist/clonegator-live_<version>/ : noyau, initrd et système compressé,
#     les fichiers du démarrage réseau (§17).
#
# Il faut root, le réseau (dépôts Debian) et, sur la machine de construction :
# mmdebstrap debian-archive-keyring squashfs-tools xorriso grub-pc-bin mtools
# dosfstools.
set -euo pipefail
cd "$(dirname "$0")/.."

DEBIAN=trixie
MIROIR=http://deb.debian.org/debian

if [ "$(id -u)" -ne 0 ]; then
    echo "La construction du live demande root." >&2
    exit 1
fi

# Le live porte exactement le paquet publié : même version, même contenu.
deb=$(./outils/construire-paquet.sh)
version=$(basename "$deb" _all.deb)
version=${version#clonegator_}
nom="clonegator-live_${version}"
echo "Paquet : $deb"

travail=$(mktemp -d /var/tmp/clonegator-live.XXXXXX)
trap 'rm -rf "$travail"' EXIT
systeme="$travail/systeme"
amorce="$travail/amorce"
iso="$travail/iso"

# ------------------------------------------------------------------ système ---

# Ce que le live contient, en plus du minimum de Debian et de CloneGator :
paquets=(
    linux-image-amd64 live-boot systemd-sysv udev kmod
    login procps                            # la console de dépannage
    keyboard-configuration console-setup    # les trois claviers, la police
    systemd-resolved iproute2               # réseau par DHCP (systemd-networkd)
    firmware-realtek firmware-bnx2          # cartes réseau courantes
)

echo "Système Debian ($DEBIAN)…"
mmdebstrap --mode=root --variant=apt --components="main non-free-firmware" \
    --include="${paquets[*]}" \
    --customize-hook="upload $deb /tmp/clonegator.deb" \
    --customize-hook='chroot "$1" env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends /tmp/clonegator.deb' \
    --customize-hook='rm "$1/tmp/clonegator.deb"' \
    --customize-hook="sync-in live/systeme /" \
    --customize-hook='chroot "$1" systemctl enable clonegator-live.service clonegator-clavier.service systemd-networkd.service systemd-resolved.service' \
    --customize-hook='chroot "$1" systemctl disable getty@tty1.service' \
    --customize-hook='chroot "$1" passwd --delete root' \
    --customize-hook='mkdir -p "$1/etc/network"' \
    --customize-hook='echo clonegator > "$1/etc/hostname"' \
    --customize-hook=': > "$1/etc/machine-id"' \
    --customize-hook='chroot "$1" update-initramfs -u -k all' \
    --customize-hook='rm -rf "$1"/var/lib/apt/lists/* "$1"/var/cache/apt/*.bin' \
    "$DEBIAN" "$systeme" "$MIROIR"

install -d "$iso/live" "$iso/boot/grub/fonts" "$iso/.disk"
cp "$(readlink -f "$systeme/vmlinuz")" "$iso/live/vmlinuz"
cp "$(readlink -f "$systeme/initrd.img")" "$iso/live/initrd.img"
echo "Système compressé…"
mksquashfs "$systeme" "$iso/live/filesystem.squashfs" -comp zstd -Xcompression-level 19 \
    -e boot -noappend -quiet

# --------------------------------------------------------------- démarrage ---

# Le shim et le GRUB signés de Debian : l'UEFI avec Secure Boot les accepte.
echo "Chargeurs signés…"
mmdebstrap --mode=root --variant=extract --include=shim-signed,grub-efi-amd64-signed \
    "$DEBIAN" "$amorce" "$MIROIR" 2>/dev/null
shim=$(find "$amorce/usr/lib/shim" -name 'shimx64.efi.signed*' | sort | tail -1)
grub_cd="$amorce/usr/lib/grub/x86_64-efi-signed/gcdx64.efi.signed"

# Ce GRUB signé cherche le support qui porte .disk/info, puis lit
# /boot/grub/grub.cfg : le même menu sert au BIOS.
echo "CloneGator live $version" > "$iso/.disk/info"
cp live/grub.cfg "$iso/boot/grub/grub.cfg"
cp /usr/share/grub/unicode.pf2 "$iso/boot/grub/fonts/"

efi="$iso/boot/grub/efi.img"
mkfs.vfat -C "$efi" 4096 >/dev/null
mmd -i "$efi" ::/EFI ::/EFI/BOOT
mcopy -i "$efi" "$shim" ::/EFI/BOOT/BOOTX64.EFI
mcopy -i "$efi" "$grub_cd" ::/EFI/BOOT/grubx64.efi

# BIOS : un GRUB autonome qui trouve l'ISO et lit le même menu. Ses modules
# restent dans l'image intégrée : ne pas déplacer $prefix vers l'ISO.
cat > "$travail/bios.cfg" <<'FIN'
search --no-floppy --file --set=root /.disk/info
configfile ($root)/boot/grub/grub.cfg
FIN
# Le cœur BIOS ne doit pas dépasser 480 Ko : seulement ces modules, et ce dont
# ils dépendent (moddep.lst), sans quoi la police et le mode graphique échouent.
modules_bios() {
    local a_voir=("$@") vus=() m
    while [ ${#a_voir[@]} -gt 0 ]; do
        m=${a_voir[0]}; a_voir=("${a_voir[@]:1}")
        [[ " ${vus[*]} " == *" $m "* ]] && continue
        vus+=("$m")
        a_voir+=($(sed -n "s/^$m: *//p" /usr/lib/grub/i386-pc/moddep.lst))
    done
    echo "${vus[*]}"
}
grub-mkstandalone --format=i386-pc --output="$travail/coeur.img" \
    --install-modules="$(modules_bios linux normal iso9660 biosdisk part_msdos part_gpt search \
        configfile font gfxterm all_video test echo)" \
    --modules="linux normal iso9660 biosdisk search configfile" \
    --locales="" --fonts="" "boot/grub/grub.cfg=$travail/bios.cfg"
cat /usr/lib/grub/i386-pc/cdboot.img "$travail/coeur.img" > "$iso/boot/grub/bios.img"

# ------------------------------------------------------------------ résultat ---

mkdir -p dist
echo "ISO…"
xorriso -as mkisofs -iso-level 3 -full-iso9660-filenames -joliet -joliet-long -rational-rock \
    -volid CLONEGATOR -output "dist/$nom.iso" \
    --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
    -partition_offset 16 --mbr-force-bootable \
    -eltorito-boot boot/grub/bios.img -no-emul-boot -boot-load-size 4 -boot-info-table \
        --grub2-boot-info --eltorito-catalog boot/grub/boot.cat \
    -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot \
    -append_partition 2 0xef "$efi" -appended_part_as_gpt \
    "$iso" 2>/dev/null

rm -rf "dist/$nom"
mkdir -p "dist/$nom"
cp "$iso/live/vmlinuz" "$iso/live/initrd.img" "$iso/live/filesystem.squashfs" "dist/$nom/"

echo "dist/$nom.iso ($(du -h "dist/$nom.iso" | cut -f1))"
