"""工单 01 实现侧用例：管理员口令、会话存储与回跳目标校验。"""

from __future__ import annotations

import pytest

from quota_dashboard import auth


def test_admin_password_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth.PASSWORD_VARIABLE, "secret-pass")
    assert auth.admin_password() == "secret-pass"
    assert auth.password_configured()


def test_admin_password_treats_blank_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(auth.PASSWORD_VARIABLE, "")
    assert auth.admin_password() is None
    assert not auth.password_configured()


def test_admin_password_unconfigured_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(auth.PASSWORD_VARIABLE, raising=False)
    assert auth.admin_password() is None


def test_verify_password_accepts_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth.PASSWORD_VARIABLE, "青岚 & A+B")
    assert auth.verify_password("青岚 & A+B")


@pytest.mark.parametrize("candidate", ["", "wrong", "青岚 & A+b"])
def test_verify_password_rejects_mismatch(
    monkeypatch: pytest.MonkeyPatch, candidate: str
) -> None:
    monkeypatch.setenv(auth.PASSWORD_VARIABLE, "青岚 & A+B")
    assert not auth.verify_password(candidate)


def test_verify_password_rejects_everything_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(auth.PASSWORD_VARIABLE, raising=False)
    assert not auth.verify_password("")
    assert not auth.verify_password("anything")


def test_session_store_roundtrip() -> None:
    store = auth.SessionStore()
    token = store.create()
    assert token
    assert store.is_valid(token)


def test_session_store_rejects_unknown_and_empty_tokens() -> None:
    store = auth.SessionStore()
    assert not store.is_valid("not-a-token")
    assert not store.is_valid("")


def test_session_store_expires_old_sessions() -> None:
    # 负的存活阈值让任何已创建会话立即过期，避免依赖系统时钟粒度。
    store = auth.SessionStore(max_age_seconds=-1.0)
    token = store.create()
    assert not store.is_valid(token)


def test_safe_next_accepts_relative_paths() -> None:
    assert auth.safe_next("/") == "/"
    assert auth.safe_next("/?view=settings") == "/?view=settings"
    assert (
        auth.safe_next("/?platform=deepseek&q=%E9%9D%92%E5%B2%9A")
        == "/?platform=deepseek&q=%E9%9D%92%E5%B2%9A"
    )


@pytest.mark.parametrize(
    "raw",
    [None, "", "https://evil.example/", "//evil.example/", "\\\\evil.example\\", "javascript:alert(1)"],
)
def test_safe_next_rejects_non_local_targets(raw: str | None) -> None:
    assert auth.safe_next(raw) == "/"


@pytest.mark.parametrize(
    "raw",
    [
        "/\r\nSet-Cookie: quota_session=attacker",
        "/\t/evil.example/",
        "/\n",
        "/\x00",
        "/\x7f",
    ],
)
def test_safe_next_rejects_control_characters(raw: str) -> None:
    # 控制字符可借 Location 头注入或被浏览器剥离后绕过 // 检查。
    assert auth.safe_next(raw) == "/"
