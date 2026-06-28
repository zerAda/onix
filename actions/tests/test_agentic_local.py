from __future__ import annotations
from app import agentic_local as ag


class _Tracker:
    def __init__(self): self.events = []
    def track(self, et, **kw): self.events.append((et, kw))


def _tool(name, result):
    return ag.Tool(name=name, description="t", parameters_schema={"type": "object", "properties": {}},
                   kind="read", gate_feature="audit", handler=lambda args: result)


def test_run_agent_happy_path_un_outil_puis_reponse():
    # Le generateur : 1er appel -> tool_call ; 2e appel -> reponse finale sourcee.
    calls = {"n": 0}
    def gen(messages, schemas):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "tool_calls": [
                {"function": {"name": "client_360", "arguments": {"client_key": "ALPHA"}}}]}
        return {"content": "Le client ALPHA a 2 taches ouvertes. Source : dossier ALPHA [1]."}
    tools = {"client_360": _tool("client_360", {"client_key": "ALPHA", "reference_trouvee": True,
                                                "sources": ["ALPHA"]})}
    tr = _Tracker()
    res = ag.run_agent("Fais le point sur ALPHA", tools=tools, generator=gen,
                       gate=lambda f: None, tracker=tr, max_steps=4)
    assert res["blocked"] is False and res["truncated"] is False
    assert [s["tool"] for s in res["steps"]] == ["client_360"]
    assert "ALPHA" in res["answer"]
    assert ("agent_completed", {"document_count": 1}) in [(e, {"document_count": k.get("document_count")})
                                                          for e, k in tr.events]
