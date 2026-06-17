"""Conftest local du paquet `ragas_eval` — active les imports en **nom plat**.

`tests/rag/` suit la convention d'imports plats (ex. ``import live_harness``) :
pytest place le dossier contenant `conftest.py` en tête du ``sys.path``. On
applique la même convention au sous-paquet `ragas_eval` afin que ``import judge``,
``import metrics`` et ``import runner`` fonctionnent à la collecte des tests,
exactement comme `live_harness` / `conftest` au niveau parent — et aussi quand on
lance `python -m ragas_eval.runner`.

On ajoute DEUX dossiers :
  * ce dossier (`tests/rag/ragas_eval`) → modules du paquet en nom plat ;
  * le dossier parent (`tests/rag`) → `live_harness` réutilisé par le juge réel.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RAG_DIR = _HERE.parent
for _p in (str(_HERE), str(_RAG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
