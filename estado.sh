#!/usr/bin/env bash
# Guarda o estado do vigia no branch "estado" (falhas abertas, linha de base, eventos e prints).
# Uso: ./estado.sh abrir | ./estado.sh salvar "mensagem"
set -u
cd "$(dirname "$0")"

case "${1:-}" in
  abrir)
    git fetch -q origin estado
    git worktree add -q -B estado estado origin/estado
    ;;
  salvar)
    cd estado || exit 1
    git add -A
    git -c user.name="vigia-black" -c user.email="vigia-black@users.noreply.github.com" \
      commit -qm "${2:-estado}" || exit 0
    for i in 1 2 3 4 5 6; do
      git push -q origin estado && exit 0
      sleep $((RANDOM % 8 + 2))
      git pull -q --rebase origin estado
    done
    echo "não consegui salvar o estado" >&2
    exit 1
    ;;
  *)
    echo "uso: $0 abrir | salvar <mensagem>" >&2
    exit 2
    ;;
esac
