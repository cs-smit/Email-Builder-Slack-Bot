from emailbot import flows
from emailbot.store import LocalStore


class FakeSlack:
    token = "xoxb-test"

    def __init__(self):
        self.posts = []

    def chat_postMessage(self, **kw):
        self.posts.append(kw["text"])
        return {"ts": "1.1"}

    def files_upload_v2(self, **kw):
        pass


def test_link_parsing():
    text = "<@U1> <https://www.figma.com/design/K/n?node-id=1-2|link> base it on welcome.html <https://b.s3.amazonaws.com/x/>"
    assert flows.figma_links(text) == ["https://www.figma.com/design/K/n?node-id=1-2"]
    assert flows.strip_urls(text) == "base it on welcome.html"
    assert flows.urls_in("see https://example.com/a, ok")[0] == "https://example.com/a"


def test_start_without_link_or_files_shows_help():
    slack = FakeSlack()
    flows.Bot(slack, LocalStore(), inline=True).start("C1", "1.0", "<@U1> hello", [])
    assert slack.posts[-1] == flows.HELP


def test_reply_while_building_is_held():
    slack = FakeSlack()
    flows.Bot(slack, LocalStore(), inline=True).follow_up("C1", "1.0", flows.PENDING, "make it blue")
    assert "still building" in slack.posts[-1]


def test_local_store_thread_map():
    store = LocalStore()
    store.set_thread("C1", "2.0", "job-1")
    assert store.get_thread("C1", "2.0") == "job-1"
    assert store.get_thread("C1", "3.0") is None


def test_reply_on_unfinished_build_restarts_it(monkeypatch, tmp_path):
    from emailbot import config, pipeline

    job = pipeline.Job(id="job-unfinished", figma_url="upload", notes="")
    job.save()  # no email.html: the first build stopped part-way
    calls = []
    monkeypatch.setattr(pipeline, "resume", lambda j, log=print: calls.append(j.id) or (_ for _ in ()).throw(RuntimeError("stop")))
    slack = FakeSlack()
    flows.Bot(slack, LocalStore(), inline=True).follow_up("C1", "1.0", "job-unfinished", "check now")
    assert calls == ["job-unfinished"]
    assert any("building this email again" in p for p in slack.posts)
