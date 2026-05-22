from types import SimpleNamespace

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily2markdown.domain_classifier import classify_domain_papers


def _client_returning(content: str):
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _config(**overrides):
    base = {
        "domain": {
            "topic": "nickelate superconductors",
            "use_ai": True,
            "ai_confidence_threshold": 0.5,
            "uncertain_policy": "audit_only",
            "canonical_vocabulary_path": None,
        },
        "llm": {
            "generation_kwargs": {"model": "test-model"},
        },
    }
    base.update(overrides)
    return OmegaConf.create(base)


def test_classify_domain_accepts_only_accept_decisions_above_threshold():
    papers = [
        make_sample_paper(title="Nickelate", arxiv_id="2605.00001v1", score=5.2),
        make_sample_paper(title="Catalyst", arxiv_id="2605.00002v1", score=5.1),
        make_sample_paper(title="Boundary", arxiv_id="2605.00003v1", score=5.0),
        make_sample_paper(title="Low confidence", arxiv_id="2605.00004v1", score=4.9),
    ]
    client = _client_returning(
        """
        {"papers":[
          {"paper_id":"2605.00001v1","is_in_domain":true,"confidence":0.91,"decision":"accept","reason":"direct nickelate","matched_concepts":["La3Ni2O7"],"negative_evidence":[]},
          {"paper_id":"2605.00002v1","is_in_domain":false,"confidence":0.88,"decision":"reject","reason":"not nickelate","matched_concepts":[],"negative_evidence":["catalyst"]},
          {"paper_id":"2605.00003v1","is_in_domain":true,"confidence":0.75,"decision":"uncertain","reason":"analogy only","matched_concepts":["cuprate"],"negative_evidence":[]},
          {"paper_id":"2605.00004v1","is_in_domain":true,"confidence":0.42,"decision":"accept","reason":"weak","matched_concepts":["nickelate"],"negative_evidence":[]}
        ]}
        """
    )

    decisions = classify_domain_papers(papers, _config(), client)

    assert [decision.paper_id for decision in decisions if decision.accepted] == ["2605.00001v1"]
    assert {decision.paper_id: decision.decision for decision in decisions} == {
        "2605.00001v1": "accept",
        "2605.00002v1": "reject",
        "2605.00003v1": "uncertain",
        "2605.00004v1": "accept",
    }
    assert decisions[-1].accepted is False


def test_classify_domain_turns_malformed_llm_output_into_uncertain_decisions():
    papers = [
        make_sample_paper(title="Paper A", arxiv_id="2605.00001v1"),
        make_sample_paper(title="Paper B", arxiv_id="2605.00002v1"),
    ]

    decisions = classify_domain_papers(papers, _config(), _client_returning("not json"))

    assert [decision.paper_id for decision in decisions] == ["2605.00001v1", "2605.00002v1"]
    assert all(decision.decision == "uncertain" for decision in decisions)
    assert all(decision.accepted is False for decision in decisions)
    assert all("malformed_json" in decision.reason for decision in decisions)


def test_classify_domain_accepts_longlist_when_ai_disabled():
    papers = [
        make_sample_paper(title="Paper A", arxiv_id="2605.00001v1"),
        make_sample_paper(title="Paper B", arxiv_id="2605.00002v1"),
    ]
    config = _config(domain={"topic": "nickelate superconductors", "use_ai": False})

    decisions = classify_domain_papers(papers, config, _client_returning("unused"))

    assert [decision.paper_id for decision in decisions if decision.accepted] == [
        "2605.00001v1",
        "2605.00002v1",
    ]
    assert all(decision.reason == "Passed local longlist filtering; AI domain filtering disabled." for decision in decisions)
