# Spell-check word lists: provenance

Built files; do not edit by hand. Rebuild with `python3 tools/build_spelling_wordlists.py` (its docstring has the arguments).

- Built: 2026-11-25, producer: deterministic (`tools/build_spelling_wordlists.py`, affix expansion in plain Python).
- Source: the Hunspell dictionaries bundled in the pyenchant 2.0.0 wheel (`enchant/share/enchant/myspell/`), read from a Python environment already on the owner's Mac; nothing was downloaded.
- Upstream: SCOWL (Spell Checker Oriented Word Lists), Kevin Atkinson, http://wordlist.aspell.net/ ; the `-large` Hunspell dictionaries are SCOWL's size-70 lists. SCOWL release: UNKNOWN (the source folder carried no README or version file).
- Licence: SCOWL's own permissive notice (use, copy, modify, distribute, with the copyright notice kept). The notice file did not travel with the source files, so its exact text is not reproduced here; it is at http://wordlist.aspell.net/scowl-readme/ .

| source file | sha256 |
|---|---|
| `en_US-large.dic` | `713dda6e92a7e3218c0d04c00647f62db4a29eb4278f83174e1a4e65fce99ff1` |
| `en_US-large.aff` | `1d1a827ce91d9eb061d5954ba325f8a538b386bd70704af431fbaac1c8e9623a` |
| `en_GB-large.dic` | `96af5a0cd3fc970055d97679b3fdd0387db9b536c7ee9948b5438e6ad996cd4f` |
| `en_GB-large.aff` | `1d1a827ce91d9eb061d5954ba325f8a538b386bd70704af431fbaac1c8e9623a` |

| built file | words | sha256 |
|---|---|---|
| `en-common.txt.gz` | 129535 | `cc8ce073bab930a150041fbb6f29bd154e2314af456814aaa918ac80eec29bce` |
| `en-us-only.txt.gz` | 1496 | `f73986f96632eec7b145529e82f2caee612c3e76a712d8bd988a2ecbd6b96362` |
| `en-gb-only.txt.gz` | 4016 | `49054b06dd9f87eeab8e0b835f745f52ce6ee2081422338df770812b4ddefc1e` |

`en-extra.txt` is not built: it is a short list kept by hand (producer: human and agent edits, reviewed in git) of general English and scholarly words the lists above lack, each met in a real manuscript. First entries: 2026-11-25, from the agent's own knowledge of their spelling, not checked against a published dictionary.
