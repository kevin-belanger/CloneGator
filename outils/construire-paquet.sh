#!/usr/bin/env bash
#
# Construit le paquet .deb de CloneGator dans dist/ (§15 de l'analyse).
#
#   ./outils/construire-paquet.sh
#
# La version du paquet est celle de clonegator/__init__.py, complétée de la
# date et de l'heure du commit, et de son hachage : ce qui s'exécute doit toujours pouvoir être retrouvé
# (§14). Un dépôt modifié mais non commité est refusé, pour la même raison.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain -- clonegator paquet)" ]; then
    echo "Des changements ne sont pas commités dans clonegator/ ou paquet/ : commitez d'abord." >&2
    exit 1
fi

base=$(python3 -c 'import clonegator; print(clonegator.VERSION)')
commit=$(git rev-parse --short HEAD)
# Date et heure du commit, à la minute : deux versions du même jour se suivent
# dans l'ordre, et apt accepte la mise à jour (le hachage seul n'est pas ordonné).
quand=$(git log -1 --format=%cd --date=format-local:%Y%m%d%H%M)
version="${base/-dev/~dev}+${quand}.g${commit}"

racine=$(mktemp -d)
trap 'rm -rf "$racine"' EXIT

install -d "$racine/DEBIAN" "$racine/usr/bin" "$racine/usr/lib/clonegator" \
           "$racine/usr/share/doc/clonegator"
git archive HEAD clonegator | tar -x -C "$racine/usr/lib/clonegator"
# La version affichée par le logiciel est exactement celle du paquet.
sed -i "s/^VERSION = .*/VERSION = \"$version\"/" "$racine/usr/lib/clonegator/clonegator/__init__.py"

install -m 755 paquet/clonegator "$racine/usr/bin/clonegator"
install -m 644 README.md "$racine/usr/share/doc/clonegator/README.md"
install -m 644 LICENSE "$racine/usr/share/doc/clonegator/copyright"
install -m 755 paquet/prerm paquet/postrm "$racine/DEBIAN/"
sed "s/@VERSION@/$version/" paquet/control > "$racine/DEBIAN/control"

mkdir -p dist
dpkg-deb --root-owner-group --build "$racine" "dist/clonegator_${version}_all.deb" >/dev/null
echo "dist/clonegator_${version}_all.deb"
