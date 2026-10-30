"""Relógio do vigia.

O agendamento do GitHub atrasa e pula rodadas, então este script fica rodando dentro de um job
(até ~5h45) e dispara cada tarefa na hora certa. No fim, o workflow religa o relógio.

  a cada 5 min      no-ar, aqui mesmo
  toda hora, :07    jornada (workflow próprio, os dois sites em paralelo)
  8h, 13h e 19h     velocidade
  10h30 e 16h30     formulario
  9h                resumo
"""
import subprocess
import sys
import time

from vigia import agora, log

DURACAO = 5 * 3600 + 45 * 60
AGENDA = {  # (hora, minuto) de Brasília -> tarefa do workflow rotinas
    (8, 0): "velocidade", (13, 0): "velocidade", (19, 0): "velocidade",
    (10, 30): "formulario", (16, 30): "formulario",
    (9, 0): "resumo",
}


def disparar(workflow, tarefa=None):
    cmd = ["gh", "workflow", "run", workflow]
    if tarefa:
        cmd += ["-f", f"tarefa={tarefa}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    log("disparo", workflow, tarefa or "", "ok" if r.returncode == 0 else r.stderr.strip()[:200])


def main():
    inicio = time.time()
    feitos = set()
    while time.time() - inicio < DURACAO:
        t = agora()
        chave_min = t.strftime("%Y%m%d%H%M")
        if t.minute % 5 == 0 and ("noar", chave_min) not in feitos:
            feitos.add(("noar", chave_min))
            subprocess.run(["git", "-C", "estado", "pull", "-q", "--rebase", "origin", "estado"], check=False)
            subprocess.run([sys.executable, "vigia.py", "no-ar"], check=False)
        if t.minute == 7 and ("jornada", chave_min) not in feitos:
            feitos.add(("jornada", chave_min))
            disparar("jornada.yml")
        tarefa = AGENDA.get((t.hour, t.minute))
        if tarefa and (tarefa, chave_min) not in feitos:
            feitos.add((tarefa, chave_min))
            disparar("rotinas.yml", tarefa)
        time.sleep(20)
    log("relógio encerrando, o workflow vai religar")


if __name__ == "__main__":
    main()
