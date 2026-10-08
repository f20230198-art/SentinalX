"""IOC extraction: defanging, validation, overlap rules, false-positive filters."""

from backend.pipeline.extract import IOCExtractor, dedupe, refang

ioc = IOCExtractor()


def values(text: str, ioc_type: str) -> set[str]:
    return {m.value for m in ioc.extract(text) if m.type == ioc_type}


def test_refang_common_styles():
    assert refang("evil[.]com 1.2.3(.)4 hxxps://x[.]io a[at]b.com") == \
        "evil.com 1.2.3.4 https://x.io a@b.com"


def test_ipv4_octets_are_validated():
    assert values("beacon to 185.220.101.45 and 999.1.1.1", "ipv4") == {"185.220.101.45"}


def test_defanged_ip_is_found():
    assert values("C2 at 45.61.184[.]220", "ipv4") == {"45.61.184.220"}


def test_cve_is_uppercased():
    assert values("poc for cve-2024-3400 works", "cve") == {"CVE-2024-3400"}


def test_sha256_is_not_also_reported_as_md5_or_sha1():
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    found = ioc.extract(f"hash: {h}")
    assert {(m.type, m.value) for m in found} == {("sha256", h)}


def test_md5_is_lowercased():
    assert values("md5 D41D8CD98F00B204E9800998ECF8427E", "md5") == \
        {"d41d8cd98f00b204e9800998ecf8427e"}


def test_btc_bech32():
    addr = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    assert values(f"pay to {addr}", "btc") == {addr}


def test_defanged_domain_is_found():
    assert values("phishlet on okta-sso[.]help still valid", "domain") == {"okta-sso.help"}


def test_domain_inside_url_is_not_double_counted():
    found = ioc.extract("grab it at https://secure-update.xyz/payload")
    assert values("grab it at https://secure-update.xyz/payload", "domain") == set()
    assert any(m.type == "url" for m in found)


def test_domain_inside_email_is_not_double_counted():
    assert values("contact ops@evil-corp.com", "domain") == set()
    assert values("contact ops@evil-corp.com", "email") == {"ops@evil-corp.com"}


def test_filenames_and_libraries_are_not_domains():
    # Public Suffix List filter: .js / .exe / .yaml are not registered TLDs.
    assert values("loader.exe needs node.js and config.yaml", "domain") == set()


def test_real_multi_label_suffix_is_a_domain():
    assert values("mirror on files.example.co.uk", "domain") == {"files.example.co.uk"}


def test_url_drops_trailing_sentence_punctuation():
    assert values("get it at hxxps://fast-paste[.]su/drop.", "url") == {"https://fast-paste.su/drop"}


def test_dedupe_keeps_first_span():
    found = ioc.extract("1.1.1.1 then 1.1.1.1 again")
    assert len(found) == 2
    assert len(dedupe(found)) == 1
