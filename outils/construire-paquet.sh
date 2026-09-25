#!/usr/bin/env bash
#
# Construit le paquet .deb de CloneGator dans dist/ (§15 de l'analyse).
#
#   ./outils/construire-paquet.sh
#
# La version du paquet est celle de clonegator/__init__.py, complétée de la
# date et du commit : ce qui s'exécute doit toujours pouvoir être retrouvé
# (§14). Un dépôt modifié mais non commité est refusé, pour la même raison.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain -- clonegator paquet)" ]; then
    echo "Des changements ne sont pas commités dans clonegator/ ou paquet/ : commitez d'abord." >&2
    exit 1
fi

base=$(python3 -c 'import clonegator; print(clonegator.VERSION)')
commit=$(git rev-parse --short HEAD)
version="${base/-dev/~dev}+$(date +%Y%m%d).g${commit}"

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
