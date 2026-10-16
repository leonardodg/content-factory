from content_factory.domain.web_urls import (
    AD_RULES_VERSION,
    ad_signal,
    canonical_url,
    canonicalize,
    domain_of,
)


def test_ad_signal_bing_aclick() -> None:
    assert ad_signal("https://www.bing.com/aclick?ld=abc") == "ad:bing_aclick"


def test_ad_signal_bing_aclk() -> None:
    assert ad_signal("https://bing.com/aclk?x=1") == "ad:bing_aclk"


def test_ad_signal_ddg_yjs() -> None:
    assert ad_signal("https://duckduckgo.com/y.js?ad=1") == "ad:ddg_yjs"


def test_ad_signal_googleadservices_and_doubleclick() -> None:
    assert ad_signal("https://www.googleadservices.com/pagead/aclk") == "ad:googleadservices"
    assert ad_signal("https://ad.doubleclick.net/ddm/trackclk") == "ad:doubleclick"


def test_ad_signal_google_aclk() -> None:
    assert ad_signal("https://www.google.com/aclk?sa=l") == "ad:google_aclk"


def test_marketplace_or_param_or_oferta_is_not_ad() -> None:
    assert ad_signal("https://www.mercadolivre.com.br/resina/p/MLB123?id=1", "Oferta") is None
    assert ad_signal("https://loja.ex.com/kit-resina?utm_source=x", "Desconto 50%") is None
    assert ad_signal("https://ex.com/produto?gclid=abc", "Comprar resina") is None


def test_ad_signal_invalid_url_is_none() -> None:
    assert ad_signal("não é url") is None
    assert ad_signal("") is None


def test_ad_rules_version_is_exposed() -> None:
    assert AD_RULES_VERSION == "ads-v1"


def test_canonical_lowercases_host_and_drops_fragment() -> None:
    c = canonicalize("https://Ex.COM/Path#frag")
    assert c.url == "https://ex.com/Path"
    assert c.ok is True


def test_canonical_removes_only_tracking_params_and_sorts() -> None:
    c = canonicalize("https://ex.com/p?b=2&utm_source=x&a=1&fbclid=z&gclid=y")
    assert c.url == "https://ex.com/p?a=1&b=2"


def test_canonical_keeps_other_params() -> None:
    c = canonicalize("https://ex.com/p?id=99&p=2&q=resina")
    assert c.url == "https://ex.com/p?id=99&p=2&q=resina"


def test_canonical_is_idempotent() -> None:
    once = canonical_url("https://Ex.com/A?utm_source=x&b=2#f")
    assert canonical_url(once) == once


def test_canonical_invalid_url_returned_as_is() -> None:
    c = canonicalize("sem-esquema.com/x")
    assert c.url == "sem-esquema.com/x"
    assert c.ok is False


def test_domain_of() -> None:
    assert domain_of("https://www.Ex.com/A") == "www.ex.com"
    assert domain_of("nope") == ""
