# KISS FM Algarve — Kodi M3U + XMLTV

Automação para a KISS FM Algarve.

## Stream

`https://nl.digitalrm.pt:8024/stream`

## Ficheiros

- `kissfm.m3u` — playlist Kodi
- `kissfm.xml` — EPG XMLTV
- `data/history.json` — histórico das músicas detetadas
- `kissfm.py` — obtém Icecast e atualiza os ficheiros
- `.github/workflows/update.yml` — executa a cada 5 minutos

## Kodi

Depois de publicares o repositório, usa:

`https://raw.githubusercontent.com/UTILIZADOR/REPOSITORIO/main/kissfm.m3u`

O M3U aponta para `kissfm.xml` através de `x-tvg-url`.

## Execução manual

```bash
python kissfm.py
```

Ou:

```bash
python kissfm.py --json
```

### Nota sobre o EPG

O Icecast fornece a música atual, mas não fornece antecipadamente a hora de fim.
O histórico é construído por observações sucessivas. Por isso, o EPG melhora à medida
que o GitHub Actions vai executando o script.
