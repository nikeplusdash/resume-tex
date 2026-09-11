"""jdsource.py: a JD arrives as a local file or a URL. No test touches the network."""
import pytest

import jdsource

GOOD_JD = (
    "Senior Product Designer at Acme. Responsibilities: lead design for the core "
    "product. Qualifications: 5+ years of experience shipping design systems. "
    "You'll work with engineers to define requirements and ship." + " padding." * 40
)


def test_local_file_is_read_unchanged(tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text(GOOD_JD)
    text, label = jdsource.load_jd(str(p))
    assert text == GOOD_JD
    assert label == "jd.txt"


def test_missing_local_file_raises(tmp_path):
    with pytest.raises(jdsource.JDError):
        jdsource.load_jd(str(tmp_path / "nope.txt"))


def test_is_url():
    assert jdsource.is_url("https://boards.greenhouse.io/acme/jobs/1")
    assert jdsource.is_url("http://example.com")
    assert not jdsource.is_url("/Users/me/jd.txt")
    assert not jdsource.is_url("jd.txt")


def test_looks_like_jd_accepts_a_real_posting():
    assert jdsource.looks_like_jd(GOOD_JD) == ""


def test_looks_like_jd_rejects_short_text():
    reason = jdsource.looks_like_jd("Sign in to view this job")
    assert "short" in reason.lower()


def test_looks_like_jd_rejects_text_without_jd_keywords():
    reason = jdsource.looks_like_jd("lorem ipsum dolor sit amet " * 40)
    assert "keyword" in reason.lower()


def test_looks_like_jd_rejects_an_spa_json_blob():
    # Eightfold/Phenom careers SPAs dump their whole app state as inline JSON;
    # it is long and "experience" appears somewhere inside, so the keyword gate
    # alone would let it through as the JD.
    blob = '{"themeOptions":{"primaryColor":"#006241"},"note":"5+ years experience"}' \
           + ',"pad":{"k":"v"}' * 400
    reason = jdsource.looks_like_jd(blob)
    assert "page data" in reason.lower()

    # a real posting that happens to mention a config value in one sentence still passes
    assert jdsource.looks_like_jd(GOOD_JD) == ""


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_url_happy_path(monkeypatch):
    html = f"<html><body><nav>menu</nav><main><p>{GOOD_JD}</p></main></body></html>"
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp(html))
    text, label = jdsource.load_jd("https://boards.greenhouse.io/acme/jobs/1")
    assert "Senior Product Designer" in text
    assert "menu" not in text          # nav is stripped
    assert "greenhouse.io" in label


def test_js_rendered_board_falls_back_to_meta_description(monkeypatch):
    # A meta description written for one specific posting is trusted on length
    # alone, not the stricter keyword gate meant for arbitrary scraped body
    # text -- a short marketing-style paragraph often never says "requirements"
    # or "responsibilities" even though it's clearly the real posting.
    meta_text = "We are hiring a Brand Designer to own how the company shows up " * 5
    html = (f'<html><head><meta name="description" content="{meta_text}"></head>'
           '<body>You need to enable JavaScript to run this app.</body></html>')
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp(html))
    text, _ = jdsource.load_jd("https://jobs.ashbyhq.com/acme/1")
    assert text.strip() == meta_text.strip()


def test_js_rendered_board_falls_back_to_json_ld(monkeypatch):
    # Greenhouse/Lever/Ashby/Workday embed the full posting in a JSON-LD
    # JobPosting block that survives even when the visible body is a JS stub.
    desc = "Responsibilities: lead brand design. Qualifications: 6 years. " * 8
    html = (
        '<html><head><script type="application/ld+json">'
        f'{{"@type": "JobPosting", "title": "Brand Designer", "description": "{desc}"}}'
        '</script></head><body>You need to enable JavaScript to run this app.</body></html>')
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp(html))
    text, _ = jdsource.load_jd("https://jobs.lever.co/acme/1")
    assert "lead brand design" in text
    assert "script" not in text.lower()          # JSON-LD wrapper is not in the output


def test_js_rendered_board_falls_back_to_og_description(monkeypatch):
    og = "We are hiring a Staff Designer to shape the product end to end. " * 6
    html = (f'<html><head><meta property="og:description" content="{og}"></head>'
            '<body>Enable JavaScript.</body></html>')
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp(html))
    text, _ = jdsource.load_jd("https://jobs.ashbyhq.com/acme/2")
    assert text.strip() == og.strip()


def test_fetch_retries_once_with_a_plain_ua_on_403(monkeypatch):
    html = f"<html><body><main><p>{GOOD_JD}</p></main></body></html>"
    calls = []

    def fake_get(url, **kw):
        calls.append(kw.get("headers", {}).get("User-Agent", ""))
        return _Resp("blocked", status=403) if len(calls) == 1 else _Resp(html)
    monkeypatch.setattr(jdsource.requests, "get", fake_get)
    text, _ = jdsource.load_jd("https://acme.wd1.myworkdayjobs.com/job/1")
    assert "Senior Product Designer" in text
    assert len(calls) == 2 and calls[0] != calls[1]      # second try, different identity


def test_login_wall_falls_back_to_paste(monkeypatch, capsys):
    monkeypatch.setattr(jdsource.requests, "get",
                        lambda *a, **k: _Resp("<html><body>Sign in</body></html>"))
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: GOOD_JD)
    text, _ = jdsource.load_jd("https://linkedin.com/jobs/view/1")
    assert text == GOOD_JD
    assert "short" in capsys.readouterr().out.lower()


def test_fetch_error_falls_back_to_paste(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(jdsource.requests, "get", boom)
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: GOOD_JD)
    text, _ = jdsource.load_jd("https://example.com/job")
    assert text == GOOD_JD


def test_declined_paste_raises(monkeypatch):
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp("nope"))
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: "")
    with pytest.raises(jdsource.JDError):
        jdsource.load_jd("https://example.com/job")


def test_read_url_list(tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("https://a.com/1\n\nhttps://b.com/2\n")
    assert jdsource.read_url_list(p) == ["https://a.com/1", "https://b.com/2"]

    q = tmp_path / "jd.txt"
    q.write_text(GOOD_JD)
    assert jdsource.read_url_list(q) == []     # a real JD is not a URL list
