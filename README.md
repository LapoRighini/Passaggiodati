# Passaggiodati

Trasferisce file personali, elenco dei programmi e configurazioni da un PC a un altro.
Richiede solo Python 3.8+ (nessuna dipendenza). Windows, Linux, macOS.

## Via chiavetta / disco esterno
```
python passaggiodati.py anteprima                 # cosa verrebbe copiato
python passaggiodati.py esporta E:\              # vecchio PC -> crea archivio
python passaggiodati.py importa E:\passaggiodati_YYYYMMDD.tar.gz   # nuovo PC
```

## Via rete (stessa LAN)
```
python passaggiodati.py ricevi                    # nuovo PC: mostra IP e codice
python passaggiodati.py invia <IP> <CODICE>       # vecchio PC
```
La rete non è cifrata: usala solo su LAN di cui ti fidi.

## Cosa viene copiato
- Cartelle personali: Desktop, Documenti, Immagini, Musica, Video, Download
- Configurazioni: `.ssh`, `.gitconfig`, VS Code, segnalibri Chrome/Edge, profilo Firefox, ecc.
- Elenco programmi: `winget export` su Windows (reinstallazione automatica con `importa`/`ricevi`),
  `apt`/`flatpak` su Linux, `Brewfile` su macOS, più `pip freeze`
- Extra: `-c PERCORSO` (ripetibile), `--solo-dati`, `--solo-config`, `--wifi` (Windows, password in chiaro)

I file già presenti sul nuovo PC non vengono sovrascritti (a meno di `--sovrascrivi`).
Nota: i programmi non si copiano come file ma si reinstallano dall'elenco.
