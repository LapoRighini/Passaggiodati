#!/usr/bin/env python3
"""Passaggiodati - trasferisce file, elenco programmi e configurazioni da un PC a un altro.

Solo libreria standard di Python (3.8+). Funziona su Windows, Linux e macOS.

Modi d'uso:
  python passaggiodati.py esporta  D:\\backup.tar.gz      # crea un archivio (es. su chiavetta/disco)
  python passaggiodati.py importa  D:\\backup.tar.gz      # ripristina sul nuovo PC
  python passaggiodati.py ricevi                          # nuovo PC: si mette in ascolto sulla rete
  python passaggiodati.py invia  192.168.1.20 123456      # vecchio PC: invia direttamente via rete
  python passaggiodati.py anteprima                       # mostra cosa verrebbe copiato
"""
import argparse
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

PORTA = 52717
HOME = Path.home()
SISTEMA = platform.system()  # Windows / Linux / Darwin

# Cartelle personali (relative alla home). Quelle mancanti vengono ignorate.
CARTELLE_DATI = [
    "Desktop", "Documents", "Documenti", "Pictures", "Immagini",
    "Music", "Musica", "Videos", "Video", "Downloads", "Scaricati",
]

# Configurazioni utili (relative alla home). Quelle mancanti vengono ignorate.
CONFIGURAZIONI = [
    ".ssh", ".gitconfig", ".bashrc", ".zshrc", ".profile", ".vimrc", ".config",
    # Windows
    "AppData/Roaming/Code/User",
    "AppData/Roaming/Mozilla/Firefox",
    "AppData/Local/Google/Chrome/User Data/Default/Bookmarks",
    "AppData/Local/Google/Chrome/User Data/Default/Preferences",
    "AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks",
    "AppData/Roaming/Microsoft/Windows/Start Menu",
    # macOS
    "Library/Application Support/Code/User",
]

# Cartelle pesanti/inutili che non vale la pena copiare
ESCLUDI = {"node_modules", "__pycache__", ".cache", "Cache", "Code Cache", "GPUCache",
           "$RECYCLE.BIN", "System Volume Information", ".Trash"}


# --------------------------------------------------------------------------- raccolta

def _percorsi(base):
    """Genera tutti i file sotto base, saltando le cartelle escluse."""
    if base.is_file():
        yield base
        return
    for radice, cartelle, files in os.walk(base):
        cartelle[:] = [c for c in cartelle if c not in ESCLUDI]
        for f in files:
            yield Path(radice) / f


def raccogli(extra=(), con_dati=True, con_config=True):
    """Ritorna la lista dei file da copiare come (percorso_assoluto, percorso_relativo_alla_home)."""
    voci = []
    if con_dati:
        voci += CARTELLE_DATI
    if con_config:
        voci += CONFIGURAZIONI
    voci += list(extra)
    visti, risultato = set(), []
    for voce in voci:
        p = Path(voce).expanduser()
        if not p.is_absolute():
            p = HOME / p
        if not p.exists():
            continue
        for f in _percorsi(p):
            try:
                rel = f.relative_to(HOME)
            except ValueError:  # fuori dalla home: lo mettiamo sotto "_esterni"
                rel = Path("_esterni") / f.drive.replace(":", "") / Path(*f.parts[1:])
            if rel not in visti and f.is_file() and not f.is_symlink():
                visti.add(rel)
                risultato.append((f, rel))
    return risultato


def _esegui(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def elenco_programmi(workdir, con_wifi=False):
    """Salva in workdir l'elenco dei programmi installati. Ritorna i file creati."""
    creati = []
    if SISTEMA == "Windows":
        out = workdir / "winget.json"
        rc, _ = _esegui(["winget", "export", "-o", str(out),
                         "--accept-source-agreements", "--include-versions"])
        if out.exists():
            creati.append(out)
        # elenco completo (anche programmi non presenti in winget) a scopo di consultazione
        rc, txt = _esegui(["winget", "list", "--accept-source-agreements"])
        if txt:
            f = workdir / "programmi_installati.txt"
            f.write_text(txt, encoding="utf-8")
            creati.append(f)
        if con_wifi:
            wd = workdir / "wifi"
            wd.mkdir(exist_ok=True)
            _esegui(["netsh", "wlan", "export", "profile", "key=clear", f"folder={wd}"])
            creati += list(wd.glob("*.xml"))
    elif SISTEMA == "Linux":
        rc, txt = _esegui(["apt-mark", "showmanual"])
        if txt:
            f = workdir / "apt-manuali.txt"
            f.write_text(txt)
            creati.append(f)
        rc, txt = _esegui(["flatpak", "list", "--app", "--columns=application"])
        if txt:
            f = workdir / "flatpak.txt"
            f.write_text(txt)
            creati.append(f)
    elif SISTEMA == "Darwin":
        f = workdir / "Brewfile"
        _esegui(["brew", "bundle", "dump", f"--file={f}", "--force"])
        if f.exists():
            creati.append(f)
    rc, txt = _esegui([sys.executable, "-m", "pip", "freeze"])
    if txt:
        f = workdir / "pip-freeze.txt"
        f.write_text(txt)
        creati.append(f)
    return creati


# --------------------------------------------------------------------------- scrittura/lettura tar

def scrivi_tar(tar, files, programmi):
    totale = len(files)
    for i, (f, rel) in enumerate(files, 1):
        try:
            tar.add(str(f), arcname="home/" + rel.as_posix(), recursive=False)
        except (OSError, PermissionError) as e:
            print(f"\n  ! saltato {f}: {e}")
        if i % 50 == 0 or i == totale:
            print(f"\r  file copiati: {i}/{totale}", end="", flush=True)
    print()
    for f in programmi:
        tar.add(str(f), arcname="programmi/" + f.name)
    meta = {"data": time.strftime("%Y-%m-%d %H:%M:%S"), "sistema": SISTEMA,
            "utente": HOME.name, "file": totale}
    mf = Path(tempfile.mkdtemp()) / "manifest.json"
    mf.write_text(json.dumps(meta, indent=2))
    tar.add(str(mf), arcname="manifest.json")


def leggi_tar(tar, sovrascrivi=False):
    dest_prog = HOME / "PassaggioDati_programmi"
    nuovi = saltati = 0
    for m in tar:
        if m.name.startswith("home/") and m.isfile():
            m.name = m.name[len("home/"):]
            bersaglio = HOME / m.name
            if bersaglio.exists() and not sovrascrivi:
                saltati += 1
                continue
            _estrai(tar, m, HOME)
            nuovi += 1
            if nuovi % 50 == 0:
                print(f"\r  file ripristinati: {nuovi}", end="", flush=True)
        elif m.name.startswith("programmi/") and m.isfile():
            m.name = m.name[len("programmi/"):]
            dest_prog.mkdir(exist_ok=True)
            _estrai(tar, m, dest_prog)
    print(f"\r  file ripristinati: {nuovi}, già presenti e saltati: {saltati}")
    return dest_prog


def _estrai(tar, membro, dest):
    try:
        tar.extract(membro, str(dest), filter="data")
    except TypeError:  # Python < 3.12
        tar.extract(membro, str(dest))


def installa_programmi(dest_prog):
    """Su Windows reinstalla i programmi da winget.json (chiede conferma)."""
    wj = dest_prog / "winget.json"
    if SISTEMA == "Windows" and wj.exists():
        if input("Reinstallare i programmi con winget? [s/N] ").lower().startswith("s"):
            subprocess.run(["winget", "import", "-i", str(wj),
                            "--accept-package-agreements", "--accept-source-agreements",
                            "--ignore-unavailable"])
    else:
        print(f"Elenchi dei programmi salvati in: {dest_prog}")
    wifi = dest_prog / "wifi"
    if SISTEMA == "Windows" and wifi.exists():
        for x in wifi.glob("*.xml"):
            _esegui(["netsh", "wlan", "add", "profile", f"filename={x}"])


# --------------------------------------------------------------------------- comandi

def _scegli(args):
    files = raccogli(args.cartella or [], not args.solo_config, not args.solo_dati)
    dim = sum(f.stat().st_size for f, _ in files)
    print(f"Trovati {len(files)} file ({dim / 1e9:.2f} GB) sotto {HOME}")
    return files


def cmd_anteprima(args):
    files = _scegli(args)
    per_cartella = {}
    for f, rel in files:
        k = rel.parts[0]
        per_cartella[k] = per_cartella.get(k, 0) + f.stat().st_size
    for k, v in sorted(per_cartella.items(), key=lambda x: -x[1]):
        print(f"  {v / 1e6:10.1f} MB  {k}")


def cmd_esporta(args):
    files = _scegli(args)
    work = Path(tempfile.mkdtemp())
    print("Raccolgo l'elenco dei programmi...")
    prog = elenco_programmi(work, args.wifi)
    dest = Path(args.destinazione)
    if dest.is_dir():
        dest = dest / f"passaggiodati_{time.strftime('%Y%m%d')}.tar.gz"
    mode = "w:gz" if str(dest).endswith(".gz") else "w"
    print(f"Creo {dest}")
    with tarfile.open(dest, mode) as tar:
        scrivi_tar(tar, files, prog)
    shutil.rmtree(work, ignore_errors=True)
    print("Fatto. Copia l'archivio sul nuovo PC e lancia: passaggiodati.py importa <archivio>")


def cmd_importa(args):
    print(f"Ripristino da {args.archivio} in {HOME}")
    with tarfile.open(args.archivio, "r:*") as tar:
        dest_prog = leggi_tar(tar, args.sovrascrivi)
    installa_programmi(dest_prog)
    print("Fatto.")


def _ip_locale():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "?"


def cmd_ricevi(args):
    codice = f"{random.SystemRandom().randrange(10**6):06d}"
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", args.porta))
    srv.listen(1)
    print(f"In attesa sul PC {_ip_locale()}:{args.porta}")
    print(f"Sul vecchio PC lancia:  python passaggiodati.py invia {_ip_locale()} {codice}")
    while True:
        conn, addr = srv.accept()
        f = conn.makefile("rb")
        if f.readline().strip().decode(errors="ignore") == codice:
            conn.sendall(b"OK\n")
            break
        conn.sendall(b"NO\n")
        conn.close()
        print(f"Codice errato da {addr[0]}, ignorato.")
    print(f"Connesso con {addr[0]}, ricevo...")
    with tarfile.open(fileobj=f, mode="r|") as tar:
        dest_prog = leggi_tar(tar, args.sovrascrivi)
    conn.close()
    installa_programmi(dest_prog)
    print("Fatto.")


def cmd_invia(args):
    files = _scegli(args)
    work = Path(tempfile.mkdtemp())
    prog = elenco_programmi(work, args.wifi)
    s = socket.create_connection((args.indirizzo, args.porta), timeout=15)
    s.sendall(args.codice.encode() + b"\n")
    if s.makefile("rb").readline().strip() != b"OK":
        sys.exit("Codice rifiutato.")
    s.settimeout(None)
    print("Invio in corso...")
    with tarfile.open(fileobj=s.makefile("wb"), mode="w|") as tar:
        scrivi_tar(tar, files, prog)
    s.shutdown(socket.SHUT_WR)
    s.close()
    shutil.rmtree(work, ignore_errors=True)
    print("Fatto.")


def main():
    p = argparse.ArgumentParser(description="Trasferisce dati, programmi e configurazioni tra PC.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def opzioni_selezione(sp):
        sp.add_argument("--cartella", "-c", action="append",
                        help="cartella/file aggiuntivo da copiare (ripetibile)")
        sp.add_argument("--solo-dati", action="store_true", help="solo cartelle personali")
        sp.add_argument("--solo-config", action="store_true", help="solo configurazioni")
        sp.add_argument("--wifi", action="store_true",
                        help="includi profili Wi-Fi (Windows, password in chiaro!)")

    a = sub.add_parser("anteprima", help="mostra cosa verrebbe copiato")
    opzioni_selezione(a)
    a.set_defaults(f=cmd_anteprima)

    a = sub.add_parser("esporta", help="crea un archivio")
    a.add_argument("destinazione", help="file .tar.gz o cartella (es. la chiavetta)")
    opzioni_selezione(a)
    a.set_defaults(f=cmd_esporta)

    a = sub.add_parser("importa", help="ripristina da un archivio")
    a.add_argument("archivio")
    a.add_argument("--sovrascrivi", action="store_true", help="sovrascrivi i file esistenti")
    a.set_defaults(f=cmd_importa)

    a = sub.add_parser("ricevi", help="nuovo PC: attende i dati via rete")
    a.add_argument("--porta", type=int, default=PORTA)
    a.add_argument("--sovrascrivi", action="store_true")
    a.set_defaults(f=cmd_ricevi)

    a = sub.add_parser("invia", help="vecchio PC: invia i dati via rete")
    a.add_argument("indirizzo", help="IP del nuovo PC")
    a.add_argument("codice", help="codice mostrato dal comando 'ricevi'")
    a.add_argument("--porta", type=int, default=PORTA)
    opzioni_selezione(a)
    a.set_defaults(f=cmd_invia)

    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
