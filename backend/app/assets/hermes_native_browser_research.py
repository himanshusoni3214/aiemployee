#!/usr/bin/env python3
import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, unquote


DEFAULT_CHROME_CANDIDATES = [
    "/opt/data/home/.agent-browser/browsers/chrome-150.0.7871.46/chrome",
    "/opt/data/home/.agent-browser/browsers/chrome-149.0.7827.54/chrome",
]
STATUS_PATH = "/opt/data/home/leads/hermes_native_browser_provider_status.json"
DEFAULT_QUERIES = [
    "Toronto independent cafe contact page",
    "Toronto specialty coffee shop contact email",
    "GTA coffee roaster wholesale contact",
    "Toronto boutique grocery buyer contact",
    "Toronto restaurant coffee program contact",
]
PUBLIC_DIRECTORY_URLS = [
    "https://indi.cafe/toronto",
]
NOMINATIM_QUERIES = [
    "cafe Toronto Ontario",
    "coffee shop Toronto Ontario",
    "espresso bar Toronto Ontario",
]
KNOWN_CHAIN_KEYWORDS = [
    "starbucks",
    "tim hortons",
    "second cup",
    "aroma espresso",
    "mcdonald",
]
FIELDNAMES = [
    "Business Name",
    "Category",
    "Website",
    "Public Email",
    "Phone",
    "Address",
    "Source URL",
    "Evidence URL",
    "Why this is a fit for Brew It by Sash",
    "Email Evidence",
    "Lead Status",
    "Lead Category",
    "Identity Needs Review",
    "Lead Quality Reason",
    "Email Ready",
    "Phone Ready",
    "Source Platform ID",
    "Evidence History",
    "Enrichment Status",
    "Missing Fields",
    "Last Enrichment Attempt",
    "Pages Checked",
    "Last Error",
    "Source File",
]
BUSINESS_SUFFIX_RE = re.compile(r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|company|co|cafe|coffee|restaurant|bar)\b\.?", re.I)


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = ""
        self._text = []
        self.title = ""
        self._in_title = False
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._href = attrs.get("href", "")
            self._text = []
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            text = clean_text(" ".join(self._text))
            self.links.append({"href": self._href, "text": text})
            self._href = ""
            self._text = []
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._href:
            self._text.append(data)
        if self._in_title:
            self.title += data
        if data.strip():
            self.text_parts.append(data)


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:80] or "research"


def domain(url):
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_email_value(value):
    email = clean_text(value).lower()
    return email if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) else ""


def normalize_phone_value(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def normalize_domain_value(value):
    text = clean_text(value).lower()
    if not text:
        return ""
    if not urlparse(text).scheme:
        text = "https://" + text
    host = urlparse(text).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":", 1)[0]


def normalize_business_value(value):
    text = clean_text(value).lower()
    text = BUSINESS_SUFFIX_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def source_platform_id(row):
    for key in ("Source Platform ID", "source_platform_id", "osm_id", "place_id", "canonical_lead_id", "stable_id"):
        value = clean_text(row.get(key))
        if value:
            return value
    return ""


def lead_identity_keys(row):
    keys = set()
    email = normalize_email_value(row.get("Public Email") or row.get("email") or row.get("public_email") or row.get("verified_public_email"))
    website = normalize_domain_value(row.get("Website") or row.get("website") or row.get("domain") or row.get("url"))
    phone = normalize_phone_value(row.get("Phone") or row.get("phone") or row.get("telephone"))
    business = normalize_business_value(row.get("Business Name") or row.get("business_name") or row.get("business") or row.get("company") or row.get("name"))
    address = normalize_business_value(row.get("Address") or row.get("address") or row.get("location") or row.get("formatted_address"))
    platform_id = source_platform_id(row)
    if platform_id:
        keys.add(f"source:{platform_id}")
    if email:
        keys.add(f"email:{email}")
    if website:
        keys.add(f"domain:{website}")
    if phone:
        keys.add(f"phone:{phone}")
    if business and address:
        keys.add(f"business-address:{business}:{address}")
    elif business and website:
        keys.add(f"business-domain:{business}:{website}")
    elif business and phone:
        keys.add(f"business-phone:{business}:{phone}")
    elif business:
        keys.add(f"business:{business}")
    return keys


def business_key(row):
    keys = sorted(lead_identity_keys(row))
    return keys[0] if keys else ""


def lead_category(row):
    business = clean_text(row.get("Business Name") or row.get("business_name") or row.get("business") or row.get("company") or row.get("name"))
    email = normalize_email_value(row.get("Public Email") or row.get("email") or row.get("public_email") or row.get("verified_public_email"))
    email_evidence = clean_text(row.get("Email Evidence") or row.get("email_evidence") or "")
    phone = normalize_phone_value(row.get("Phone") or row.get("phone") or row.get("telephone"))
    website = normalize_domain_value(row.get("Website") or row.get("website") or row.get("domain") or row.get("url"))
    evidence = clean_text(row.get("Evidence URL") or row.get("Source URL") or row.get("evidence_url") or row.get("source_url") or "")
    if not business:
        return "invalid", "missing_business_name"
    if email and email_evidence:
        return "email_ready", "public_email_with_source_evidence"
    if email and not email_evidence:
        return "enrichment_needed", "email_missing_public_evidence"
    if phone:
        return "phone_ready", "public_phone_no_usable_email"
    if website or evidence:
        return "enrichment_needed", "identity_has_source_but_missing_email_and_phone"
    return "unreachable", "identity_without_contact_or_usable_source"


def finalize_lead(row):
    category, reason = lead_category(row)
    row["Lead Category"] = category
    row["Lead Quality Reason"] = reason
    row["Email Ready"] = "true" if category == "email_ready" else "false"
    row["Phone Ready"] = "true" if category == "phone_ready" else "false"
    if "Identity Needs Review" not in row:
        row["Identity Needs Review"] = "false"
    if not row.get("Lead Status") or str(row.get("Lead Status")).lower().startswith("generated"):
        row["Lead Status"] = category
    for field in FIELDNAMES:
        row.setdefault(field, "")
    return row


def missing_fields(row):
    missing = []
    if not clean_text(row.get("Business Name")):
        missing.append("business_name")
    if not normalize_email_value(row.get("Public Email")):
        missing.append("public_email")
    if normalize_email_value(row.get("Public Email")) and not clean_text(row.get("Email Evidence")):
        missing.append("email_evidence")
    if not normalize_phone_value(row.get("Phone")):
        missing.append("phone")
    if not normalize_domain_value(row.get("Website")):
        missing.append("website")
    return missing


def evidence_entry(row, source):
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "source_url": clean_text(row.get("Source URL")),
        "evidence_url": clean_text(row.get("Evidence URL")),
        "email_evidence": clean_text(row.get("Email Evidence")),
        "category": row.get("Lead Category") or lead_category(row)[0],
    }
    return {k: v for k, v in payload.items() if v}


def _history_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def merge_lead(existing_row, incoming_row, source_label):
    existing = finalize_lead(dict(existing_row or {}))
    incoming = finalize_lead(dict(incoming_row or {}))
    merged = dict(existing)
    changed_fields = []
    better_fields = [
        "Public Email",
        "Email Evidence",
        "Phone",
        "Website",
        "Address",
        "Business Name",
        "Source URL",
        "Evidence URL",
        "Source Platform ID",
    ]
    for field in better_fields:
        old = clean_text(merged.get(field))
        new = clean_text(incoming.get(field))
        if new and not old:
            merged[field] = new
            changed_fields.append(field)
        elif new and old and new != old and field in {"Email Evidence", "Evidence URL", "Source URL"}:
            history = _history_list(merged.get("Evidence History"))
            history.append({"field": field, "previous": old, "replacement": new, "at": datetime.now(timezone.utc).isoformat()})
            merged["Evidence History"] = json.dumps(history[-20:], sort_keys=True)
            merged[field] = new
            changed_fields.append(field)
    history = _history_list(merged.get("Evidence History"))
    entry = evidence_entry(incoming, source_label)
    if entry and entry not in history:
        history.append(entry)
        merged["Evidence History"] = json.dumps(history[-20:], sort_keys=True)
    before_category = existing.get("Lead Category") or lead_category(existing)[0]
    merged = finalize_lead(merged)
    after_category = merged.get("Lead Category") or lead_category(merged)[0]
    merged["Missing Fields"] = ",".join(missing_fields(merged))
    merged["Last Enrichment Attempt"] = datetime.now(timezone.utc).isoformat()
    merged["Enrichment Status"] = "enriched" if changed_fields or before_category != after_category else clean_text(existing.get("Enrichment Status")) or "unchanged"
    return merged, bool(changed_fields or before_category != after_category), changed_fields


def active_email_ready_count(rows):
    blocked = {"previously_rejected", "do_not_contact", "previously_sent", "duplicate", "invalid"}
    count = 0
    for row in rows:
        row = finalize_lead(dict(row))
        category = row.get("Lead Category") or lead_category(row)[0]
        status = clean_text(row.get("Lead Status")).lower()
        if category == "email_ready" and status not in blocked and status not in {"rejected", "sent", "do_not_contact", "unsubscribed"}:
            count += 1
    return count

def find_chrome():
    for candidate in DEFAULT_CHROME_CANDIDATES:
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    raise SystemExit("ERROR hermes_native_browser_unavailable: Chrome binary not found in Hermes browser profile")


def chrome_dom(chrome, url, timeout=35):
    args = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-sync",
        "--no-first-run",
        "--dump-dom",
        url,
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"Chrome failed for {url}").strip()[:500])
    return result.stdout


def parse_html(source):
    parser = LinkParser()
    parser.feed(source or "")
    return parser


def decode_search_url(href):
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if parsed.scheme in {"http", "https"}:
        return href
    return ""


def is_candidate_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    blocked_hosts = [
        "google.", "duckduckgo.", "bing.", "ecosia.", "facebook.", "instagram.",
        "youtube.", "tiktok.", "linkedin.", "wikipedia.", "yelp.", "tripadvisor.",
        "ubereats.", "doordash.", "skipthedishes.", "reddit.",
    ]
    return not any(item in host for item in blocked_hosts)


def is_social_or_map_url(url):
    host = domain(url)
    return any(
        item in host
        for item in [
            "instagram.com",
            "facebook.com",
            "google.com",
            "maps.google.",
            "x.com",
            "twitter.com",
            "tiktok.com",
            "youtube.com",
        ]
    )


def plain_json_from_dom(source):
    text = re.sub(r"<[^>]+>", "", source or "", flags=re.S)
    return html.unescape(text).strip()


def is_known_chain(name):
    lower = clean_text(name).lower()
    return any(item in lower for item in KNOWN_CHAIN_KEYWORDS)


def search_urls(chrome, query, max_results=10):
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    urls = []
    try:
        source = chrome_dom(chrome, url)
        parser = parse_html(source)
        page_text = clean_text(" ".join(parser.text_parts)).lower()
        if "unfortunately, bots use duckduckgo too" not in page_text:
            for link in parser.links:
                target = decode_search_url(link["href"])
                if is_candidate_url(target) and target not in urls:
                    urls.append(target)
                if len(urls) >= max_results:
                    break
    except Exception:
        urls = []
    if len(urls) < 3:
        for directory_url in PUBLIC_DIRECTORY_URLS:
            try:
                parser = parse_html(chrome_dom(chrome, directory_url))
                for link in parser.links:
                    target = decode_search_url(urljoin(directory_url, link["href"]))
                    if domain(target) == domain(directory_url):
                        continue
                    if is_candidate_url(target) and target not in urls:
                        urls.append(target)
                    if len(urls) >= max_results:
                        break
            except Exception:
                continue
            if len(urls) >= max_results:
                break
    return urls


def jsonld_objects(source):
    objects = []
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        source or "",
        flags=re.I | re.S,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            objects.extend(data)
        elif isinstance(data, dict):
            objects.append(data)
    return objects


def itemlist_entries(data):
    if isinstance(data, dict):
        if data.get("@type") == "ItemList" and isinstance(data.get("itemListElement"), list):
            for element in data["itemListElement"]:
                if isinstance(element, dict):
                    item = element.get("item")
                    if isinstance(item, dict):
                        yield item
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from itemlist_entries(item)
    elif isinstance(data, list):
        for item in data:
            yield from itemlist_entries(item)


def directory_rows_from_jsonld(chrome, directory_url, source, args):
    rows = []
    skipped = []
    excluded = [item.strip().lower() for item in re.split(r"[,;\n]+", args.exclusions or "") if item.strip()]
    for obj in jsonld_objects(source):
        for item in itemlist_entries(obj):
            name = clean_text(item.get("name"))
            address = clean_text(item.get("address"))
            website = clean_text(item.get("url"))
            map_url = clean_text(item.get("hasMap"))
            haystack = f"{name} {address}".lower()
            if not name:
                skipped.append({"url": directory_url, "reason": "directory_item_missing_name"})
                continue
            if is_known_chain(name):
                skipped.append({"url": directory_url, "business": name, "reason": "known_chain_excluded"})
                continue
            if any(term and term in haystack for term in excluded if term not in {"already contacted businesses", "already-contacted businesses"}):
                skipped.append({"url": directory_url, "business": name, "reason": "directory_item_excluded"})
                continue

            evidence_url = directory_url
            public_website = ""
            email = ""
            phone = ""
            email_evidence = ""
            candidate_url = website if website and not is_social_or_map_url(website) else ""
            if candidate_url and is_candidate_url(candidate_url):
                public_website = f"{urlparse(candidate_url).scheme}://{domain(candidate_url)}"
                lead, error = extract_business(chrome, candidate_url, args)
                if lead:
                    email = lead.get("Public Email", "")
                    phone = lead.get("Phone", "")
                    email_evidence = lead.get("Email Evidence", "")
                    evidence_url = lead.get("Evidence URL") or candidate_url
                elif error:
                    skipped.append({"url": candidate_url, "business": name, "reason": error})
            elif website:
                evidence_url = website

            rows.append({
                "Business Name": name,
                "Category": "AI Internet Research / Cafe or hospitality lead",
                "Website": public_website,
                "Public Email": email,
                "Phone": phone,
                "Address": address,
                "Source URL": directory_url,
                "Evidence URL": evidence_url or map_url or directory_url,
                "Why this is a fit for Brew It by Sash": "Public directory evidence from indi.cafe Toronto for an independent cafe or coffee shop lead.",
                "Email Evidence": email_evidence,
                "Lead Status": "Generated" if email else "Generated - email_missing_or_not_visible",
            })
    return rows, skipped


def nominatim_url(query, limit=20):
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": str(limit),
        "addressdetails": "1",
        "extratags": "1",
        "namedetails": "1",
    }
    return "https://nominatim.openstreetmap.org/search?" + "&".join(
        f"{key}={quote_plus(value)}" for key, value in params.items()
    )


def osm_evidence_url(item):
    osm_type = clean_text(item.get("osm_type"))
    osm_id = clean_text(item.get("osm_id"))
    if osm_type and osm_id:
        return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    return ""


def normalized_public_website(value):
    value = clean_text(value)
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not urlparse(value).scheme:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if is_social_or_map_url(value):
        return ""
    return f"{parsed.scheme}://{domain(value)}"


def first_public_email(*values):
    for value in values:
        emails = visible_emails(clean_text(value))
        if emails:
            return emails[0]
    return ""


def rows_from_nominatim(chrome, args, limit=20):
    rows = []
    skipped = []
    excluded = [item.strip().lower() for item in re.split(r"[,;\n]+", args.exclusions or "") if item.strip()]
    for query in NOMINATIM_QUERIES:
        url = nominatim_url(query, limit=limit)
        try:
            payload = json.loads(plain_json_from_dom(chrome_dom(chrome, url, timeout=35)))
        except Exception as exc:
            skipped.append({"url": url, "reason": f"nominatim_failed: {exc}"})
            continue
        if not isinstance(payload, list):
            skipped.append({"url": url, "reason": "nominatim_unexpected_payload"})
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("name"))
            display_name = clean_text(item.get("display_name"))
            haystack = f"{name} {display_name}".lower()
            if not name:
                skipped.append({"url": url, "reason": "nominatim_item_missing_name"})
                continue
            if is_known_chain(name):
                skipped.append({"url": url, "business": name, "reason": "known_chain_excluded"})
                continue
            if any(term and term in haystack for term in excluded if term not in {"already contacted businesses", "already-contacted businesses"}):
                skipped.append({"url": url, "business": name, "reason": "nominatim_item_excluded"})
                continue
            extratags = item.get("extratags") if isinstance(item.get("extratags"), dict) else {}
            evidence_url = osm_evidence_url(item) or url
            email = first_public_email(extratags.get("email"), extratags.get("contact:email"))
            phone = clean_text(extratags.get("phone") or extratags.get("contact:phone"))
            website = normalized_public_website(extratags.get("website") or extratags.get("contact:website") or extratags.get("url"))
            email_evidence = evidence_url if email else ""
            if website:
                lead, error = extract_business(chrome, website, args)
                if lead:
                    email = email or lead.get("Public Email", "")
                    phone = phone or lead.get("Phone", "")
                    email_evidence = email_evidence or lead.get("Email Evidence", "")
                    evidence_url = lead.get("Evidence URL") or evidence_url
                elif error:
                    skipped.append({"url": website, "business": name, "reason": error})
            rows.append({
                "Business Name": name,
                "Category": "AI Internet Research / Cafe or hospitality lead",
                "Website": website,
                "Public Email": email,
                "Phone": phone,
                "Address": display_name,
                "Source URL": url,
                "Evidence URL": evidence_url,
                "Why this is a fit for Brew It by Sash": "Public OpenStreetMap/Nominatim evidence for a Toronto cafe, coffee shop, or espresso bar lead.",
                "Email Evidence": email_evidence,
                "Lead Status": "Generated" if email else "Generated - email_missing_or_not_visible",
            })
            if len(rows) >= limit:
                return rows, skipped
    return rows, skipped


def visible_emails(text):
    emails = []
    for email in re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, flags=re.I):
        lower = email.lower()
        if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            continue
        if any(bad in lower for bad in ["example.com", "test.com", "domain.com", "noreply", "no-reply"]):
            continue
        if lower in {"user@domain.com", "name@domain.com", "email@domain.com"}:
            continue
        if lower not in emails:
            emails.append(lower)
    return emails


def visible_phone(text):
    match = re.search(r"(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}", text)
    return clean_text(match.group(0)) if match else ""


def likely_contact_links(base_url, parser):
    links = []
    for link in parser.links:
        text = (link.get("text") or "").lower()
        href = link.get("href") or ""
        if any(word in text or word in href.lower() for word in ["contact", "about", "wholesale", "locations"]):
            target = urljoin(base_url, href)
            if is_candidate_url(target) and domain(target) == domain(base_url) and target not in links:
                links.append(target)
        if len(links) >= 4:
            break
    return links


def business_name_from(title, url):
    title = clean_text(title)
    if title:
        title = re.split(r"\s[-|–]\s| - | \\| ", title)[0].strip()
        if title:
            return title, False
    fallback = domain(url).split(".")[0].replace("-", " ").title()
    return fallback, True

def fit_reason(args, url, text):
    parts = [args.target_customer or "target customer", args.product_service or args.industry, args.geography]
    reason = f"Public website found for {', '.join(p for p in parts if p)}."
    lower = text.lower()
    if "coffee" in lower or "cafe" in lower or "espresso" in lower:
        reason += " Page text references coffee/cafe service."
    return reason


def extract_business(chrome, url, args):
    try:
        source = chrome_dom(chrome, url)
    except Exception as exc:
        return None, f"inaccessible: {exc}"
    parser = parse_html(source)
    text = clean_text(" ".join(parser.text_parts))
    lower = text.lower()
    excluded = [item.strip().lower() for item in re.split(r"[,;\n]+", args.exclusions or "") if item.strip()]
    if any(item and item in lower for item in excluded if item not in {"already contacted businesses", "already-contacted businesses"}):
        return None, "excluded_by_page_text"
    contact_urls = likely_contact_links(url, parser)
    evidence_url = url
    email = ""
    phone = visible_phone(text)
    email_evidence = ""
    pages_checked = 0
    for current_url in [url] + contact_urls:
        if pages_checked >= max(1, int(getattr(args, "max_enrichment_pages_per_lead", 5) or 5)):
            break
        try:
            current_source = source if current_url == url else chrome_dom(chrome, current_url)
            pages_checked += 1
            current_parser = parse_html(current_source)
            current_text = clean_text(" ".join(current_parser.text_parts))
            emails = visible_emails(current_text)
            if emails and not email:
                email = emails[0]
                email_evidence = current_url
                evidence_url = current_url
            if not phone:
                phone = visible_phone(current_text)
            if email and phone:
                break
        except Exception:
            continue
    name, identity_needs_review = business_name_from(parser.title, url)
    if not name or not domain(url):
        return None, "missing_business_identity"
    row = {
        "Business Name": name,
        "Category": "AI Internet Research / Cafe or hospitality lead",
        "Website": f"{urlparse(url).scheme}://{domain(url)}",
        "Public Email": email,
        "Phone": phone,
        "Address": "",
        "Source URL": url,
        "Evidence URL": evidence_url,
        "Why this is a fit for Brew It by Sash": fit_reason(args, url, text),
        "Email Evidence": email_evidence,
        "Lead Status": "Generated" if email else "Generated - email_missing_or_not_visible",
        "Identity Needs Review": "true" if identity_needs_review else "false",
    }
    return finalize_lead(row), ""

def load_existing_rows(leads_dir):
    by_key = {}
    rows = []
    for path in sorted(Path(leads_dir).glob("leads_brew_it*.csv"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    row = finalize_lead(dict(row))
                    row.setdefault("Source File", str(path))
                    rows.append(row)
                    for key in lead_identity_keys(row):
                        by_key.setdefault(key, row)
        except Exception:
            continue
    return by_key, rows


def load_existing_keys(leads_dir):
    by_key, _rows = load_existing_rows(leads_dir)
    return set(by_key.keys())


def load_config(path):
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def queries_from_config(config):
    plan = config.get("source_plan") if isinstance(config.get("source_plan"), dict) else {}
    queries = [clean_text(q) for q in (plan.get("search_queries") if isinstance(plan.get("search_queries"), list) else []) if clean_text(q)] or list(DEFAULT_QUERIES)
    geographies = ["Toronto", "Scarborough", "North York", "Etobicoke", "Mississauga", "Brampton", "Markham", "Vaughan", "Richmond Hill", "Pickering", "Ajax", "Oakville", "Burlington"]
    targets = ["independent cafe", "coffee roaster", "espresso bar", "bakery cafe", "Ethiopian restaurant", "Eritrean restaurant", "boutique grocery", "specialty food store", "hotel food and beverage", "catering company", "restaurant coffee program", "wholesale coffee buyer"]
    for geo in geographies:
        for target in targets:
            queries.append(f"{geo} {target} public contact email")
            queries.append(f"{geo} {target} website contact page")
    return list(dict.fromkeys(q for q in queries if q))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_status(payload, path=STATUS_PATH):
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def provider_test(args):
    chrome = find_chrome()
    started = datetime.now(timezone.utc).isoformat()
    result = {
        "provider": "hermes_native_browser",
        "browser_binary": chrome,
        "browser_binary_detected": True,
        "browser_launch": False,
        "internet_access": False,
        "safe_test_query": args.query,
        "results_found": 0,
        "business_pages_opened": 0,
        "evidence_path": "",
        "provider_error": "",
        "tested_at": started,
    }
    evidence = []
    try:
        dom = chrome_dom(chrome, "https://example.com")
        result["browser_launch"] = "Example Domain" in dom
        result["internet_access"] = result["browser_launch"]
        urls = search_urls(chrome, args.query, max_results=6)
        result["results_found"] = len(urls)
        for url in urls[:3]:
            lead, error = extract_business(chrome, url, args)
            evidence.append({"url": url, "lead": lead, "error": error})
            if lead:
                result["business_pages_opened"] += 1
        evidence_path = Path(args.output_dir) / f"provider_test_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
        result["evidence_path"] = str(evidence_path)
    except Exception as exc:
        result["provider_error"] = str(exc)
    result["ok"] = bool(result["browser_launch"] and result["internet_access"] and result["results_found"] >= 3 and result["business_pages_opened"] >= 1)
    write_status(result, args.status_path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


def research(args):
    started_at = time.monotonic()
    config = load_config(args.config)
    chrome = find_chrome()
    leads_dir = Path(args.output_dir)
    existing_by_key, existing_rows = load_existing_rows(leads_dir)
    seen_in_run = set()
    queries = queries_from_config(config)
    rows = []
    unchanged_duplicates = 0
    invalid = 0
    skipped = []
    queries_attempted = 0
    pages_opened = 0
    no_progress_queries = 0
    stop_reason = "sources_exhausted"
    new_unique_created = 0
    existing_enriched = 0
    enrichment_attempted = 0
    enrichment_successful = 0
    new_email_ready_created = 0
    email_ready_upgraded_existing = 0
    enriched_existing_ids = set()
    email_ready_upgrade_ids = set()
    rejected_skipped = 0
    dnc_skipped = 0
    previously_sent_skipped = 0

    target_type = clean_text(getattr(args, "target_type", "email_ready") or "email_ready")
    target = int(args.limit or 25)
    max_pages = int(getattr(args, "max_pages", 150) or 150)
    max_runtime_seconds = int(getattr(args, "max_runtime_seconds", 900) or 900)
    no_progress_threshold = int(getattr(args, "max_consecutive_no_new_queries", 3) or 3)
    existing_email_ready_override = getattr(args, "existing_email_ready", None)
    email_ready_before = int(existing_email_ready_override) if existing_email_ready_override is not None else active_email_ready_count(existing_rows)
    remaining_needed = max(0, target - email_ready_before) if target_type == "email_ready" else target

    def category_counts():
        result = {key: 0 for key in ["email_ready", "phone_ready", "enrichment_needed", "unreachable", "invalid", "enrichment_exhausted"]}
        for row in rows:
            category = row.get("Lead Category") or lead_category(row)[0]
            if clean_text(row.get("Enrichment Status")) == "enrichment_exhausted":
                result["enrichment_exhausted"] += 1
            result[category] = result.get(category, 0) + 1
        result["invalid"] += invalid
        return result

    def email_ready_added():
        return new_email_ready_created + email_ready_upgraded_existing

    def email_ready_after():
        return email_ready_before + email_ready_added()

    def should_stop():
        nonlocal stop_reason
        if target_type == "email_ready" and email_ready_after() >= target:
            stop_reason = "target_achieved"
            return True
        if pages_opened >= max_pages:
            stop_reason = "max_pages_reached"
            return True
        if time.monotonic() - started_at >= max_runtime_seconds:
            stop_reason = "max_runtime_reached"
            return True
        if no_progress_queries >= no_progress_threshold:
            stop_reason = "no_new_or_enriched_leads"
            return True
        return False

    def blocked_status(row):
        status = clean_text(row.get("Lead Status") or row.get("state") or "").lower()
        category = clean_text(row.get("Lead Category")).lower()
        if status in {"rejected", "previously_rejected"} or category == "previously_rejected":
            return "previously_rejected"
        if status in {"do_not_contact", "unsubscribed"} or category == "do_not_contact":
            return "do_not_contact"
        if status in {"sent", "contacted", "previously_sent"} or category == "previously_sent":
            return "previously_sent"
        return ""

    def find_existing(keys):
        for key in keys:
            if key in existing_by_key:
                return existing_by_key[key]
        return None

    def record_keys(row, target_row):
        for key in lead_identity_keys(row):
            existing_by_key[key] = target_row
            seen_in_run.add(key)

    def add_lead(lead, source_label):
        nonlocal unchanged_duplicates, invalid, new_unique_created, existing_enriched
        nonlocal rejected_skipped, dnc_skipped, previously_sent_skipped
        nonlocal new_email_ready_created, email_ready_upgraded_existing
        if not lead:
            return False
        lead = finalize_lead(lead)
        category = lead.get("Lead Category") or "invalid"
        if category == "invalid":
            invalid += 1
            skipped.append({"url": lead.get("Source URL", ""), "business": lead.get("Business Name", ""), "reason": lead.get("Lead Quality Reason", "invalid")})
            return False
        keys = lead_identity_keys(lead)
        if not keys:
            invalid += 1
            skipped.append({"url": lead.get("Source URL", ""), "business": lead.get("Business Name", ""), "reason": "missing_stable_identity"})
            return False
        existing = find_existing(keys)
        if existing:
            block = blocked_status(existing)
            if block == "previously_rejected":
                rejected_skipped += 1
                return False
            if block == "do_not_contact":
                dnc_skipped += 1
                return False
            if block == "previously_sent":
                previously_sent_skipped += 1
                return False
            before_category = existing.get("Lead Category") or lead_category(existing)[0]
            merged, improved, _fields = merge_lead(existing, lead, source_label)
            if improved:
                existing.clear(); existing.update(merged)
                rows.append(merged)
                record_keys(merged, existing)
                existing_id = id(existing)
                if existing_id not in enriched_existing_ids:
                    enriched_existing_ids.add(existing_id)
                    existing_enriched += 1
                after_category = merged.get("Lead Category") or lead_category(merged)[0]
                if before_category != "email_ready" and after_category == "email_ready" and existing_id not in email_ready_upgrade_ids:
                    email_ready_upgrade_ids.add(existing_id)
                    email_ready_upgraded_existing += 1
                return True
            unchanged_duplicates += 1
            return False
        if keys.intersection(seen_in_run):
            unchanged_duplicates += 1
            return False
        lead["Missing Fields"] = ",".join(missing_fields(lead))
        lead["Enrichment Status"] = "new"
        lead["Last Enrichment Attempt"] = datetime.now(timezone.utc).isoformat()
        rows.append(lead)
        new_unique_created += 1
        if (lead.get("Lead Category") or lead_category(lead)[0]) == "email_ready":
            new_email_ready_created += 1
        record_keys(lead, lead)
        return True

    enrichment_candidates = [row for row in existing_rows if (row.get("Lead Category") in {"enrichment_needed", "phone_ready"} or row.get("Identity Needs Review") == "true") and not blocked_status(row)]
    for row in enrichment_candidates[: max(0, min(remaining_needed * 3 or 10, 50))]:
        if should_stop():
            break
        website = clean_text(row.get("Website"))
        if not website:
            continue
        enrichment_attempted += 1
        pages_opened += 1
        lead, error = extract_business(chrome, website, args)
        if lead:
            if add_lead({**row, **{k: v for k, v in lead.items() if clean_text(v)}}, "enrichment_existing"):
                enrichment_successful += 1
        else:
            row["Last Enrichment Attempt"] = datetime.now(timezone.utc).isoformat()
            row["Last Error"] = error
            row["Pages Checked"] = str(int(row.get("Pages Checked") or 0) + 1)
            if int(row.get("Pages Checked") or 0) >= 3:
                row["Enrichment Status"] = "enrichment_exhausted"
                rows.append(finalize_lead(dict(row)))

    for directory_url in PUBLIC_DIRECTORY_URLS:
        if should_stop():
            break
        before_progress = new_unique_created + existing_enriched
        try:
            directory_source = chrome_dom(chrome, directory_url)
            pages_opened += 1
            directory_rows, directory_skipped = directory_rows_from_jsonld(chrome, directory_url, directory_source, args)
            skipped.extend(directory_skipped)
            for lead in directory_rows:
                add_lead(lead, "directory")
                if should_stop():
                    break
        except Exception as exc:
            skipped.append({"url": directory_url, "reason": f"directory_jsonld_failed: {exc}"})
        no_progress_queries = no_progress_queries + 1 if new_unique_created + existing_enriched == before_progress else 0

    if not should_stop():
        before_progress = new_unique_created + existing_enriched
        nominatim_rows, nominatim_skipped = rows_from_nominatim(chrome, args, limit=max(remaining_needed * 3, 25))
        skipped.extend(nominatim_skipped)
        for lead in nominatim_rows:
            add_lead(lead, "nominatim")
            if should_stop():
                break
        no_progress_queries = no_progress_queries + 1 if new_unique_created + existing_enriched == before_progress else 0

    for query in queries:
        if should_stop():
            break
        queries_attempted += 1
        before_progress = new_unique_created + existing_enriched
        candidates = search_urls(chrome, query, max_results=max(remaining_needed * 4, 20))
        for url in candidates:
            if should_stop():
                break
            pages_opened += 1
            lead, error = extract_business(chrome, url, args)
            if not lead:
                skipped.append({"url": url, "reason": error})
                continue
            add_lead(lead, "search")
        no_progress_queries = no_progress_queries + 1 if new_unique_created + existing_enriched == before_progress else 0

    if stop_reason == "sources_exhausted" and target_type == "email_ready" and email_ready_after() >= target:
        stop_reason = "target_achieved"
    counts = category_counts()
    target_achieved = email_ready_after() >= target if target_type == "email_ready" else len(rows) >= target
    if target_achieved:
        status = "completed_target_achieved"
    elif new_unique_created or existing_enriched:
        status = "completed_partial"
    else:
        status = "no_new_unique_leads"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = leads_dir if rows else leads_dir / "partial"
    filename_prefix = "leads_brew_it_browser" if rows else "partial_bibs_browser"
    output = output_dir / f"{filename_prefix}_{stamp}.csv"
    write_csv(output, rows)
    metadata = {
        "provider": "hermes_native_browser",
        "output_path": str(output),
        "target": target,
        "target_type": target_type,
        "active_email_ready": email_ready_before,
        "email_ready_before": email_ready_before,
        "approved_unsent": 0,
        "draft_ready_unsent": 0,
        "remaining_needed": max(0, target - email_ready_after()),
        "remaining_to_target": max(0, target - email_ready_after()),
        "target_achieved": target_achieved,
        "status": status,
        "stop_reason": stop_reason,
        "total_discovered": new_unique_created + existing_enriched + unchanged_duplicates + invalid + rejected_skipped + dnc_skipped + previously_sent_skipped,
        "new_unique_businesses": new_unique_created,
        "new_unique_created": new_unique_created,
        "existing_enriched": existing_enriched,
        "enriched_existing": existing_enriched,
        "unchanged_duplicates": unchanged_duplicates,
        "duplicates_skipped": unchanged_duplicates,
        "email_ready": counts.get("email_ready", 0),
        "email_ready_added": email_ready_added(),
        "email_ready_after": email_ready_after(),
        "phone_ready": counts.get("phone_ready", 0),
        "enrichment_needed": counts.get("enrichment_needed", 0),
        "enrichment_exhausted": counts.get("enrichment_exhausted", 0),
        "unreachable": counts.get("unreachable", 0),
        "invalid": counts.get("invalid", 0),
        "rejected_skipped": rejected_skipped,
        "DNC_skipped": dnc_skipped,
        "previously_sent_skipped": previously_sent_skipped,
        "enrichment_attempted": enrichment_attempted,
        "enrichment_successful": enrichment_successful,
        "queries_attempted": queries_attempted,
        "pages_opened": pages_opened,
        "skipped": skipped[:80],
        "queries": queries[:120],
        "email_sending": False,
        "prospect_emails_sent": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = output.with_suffix(".metadata.json")
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(f"HERMES_NATIVE_BROWSER_OUTPUT path={output}")
    print(f"HERMES_NATIVE_BROWSER_METADATA path={meta_path}")
    for key in ["target", "target_type", "active_email_ready", "remaining_needed", "target_achieved", "new_unique_created", "existing_enriched", "unchanged_duplicates", "email_ready_before", "email_ready_added", "email_ready_after", "remaining_to_target", "enrichment_attempted", "enrichment_successful", "rejected_skipped", "DNC_skipped", "previously_sent_skipped", "queries_attempted", "pages_opened", "stop_reason", "status"]:
        print(f"{key.upper()}={metadata[key]}")
    print("PROSPECT_EMAILS_SENT=0")
    if rows or target_achieved:
        return 0
    print(f"ERROR no_new_unique_leads: no new or enriched businesses found. Partial preserved at {output}", file=sys.stderr)
    return 3

def parse_args():
    parser = argparse.ArgumentParser(description="Hermes Native Browser Internet Research provider")
    parser.add_argument("--provider-test", action="store_true")
    parser.add_argument("--query", default="Toronto independent cafe contact page")
    parser.add_argument("--company-id", default="")
    parser.add_argument("--campaign-id", default="")
    parser.add_argument("--industry", default="")
    parser.add_argument("--product-service", default="")
    parser.add_argument("--target-customer", default="")
    parser.add_argument("--geography", default="")
    parser.add_argument("--exclusions", default="")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--min-success", type=int, default=10)
    parser.add_argument("--target-type", default="email_ready")
    parser.add_argument("--existing-email-ready", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=int, default=900)
    parser.add_argument("--max-pages", type=int, default=150)
    parser.add_argument("--max-consecutive-no-new-queries", type=int, default=3)
    parser.add_argument("--max-enrichment-pages-per-lead", type=int, default=5)
    parser.add_argument("--output-dir", default="/opt/data/home/leads")
    parser.add_argument("--config", default="/opt/data/home/leads/bibs_real_lead_source_config.json")
    parser.add_argument("--status-path", default=STATUS_PATH)
    parser.add_argument("--no-email", action="store_true", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    args.limit = max(1, min(int(args.limit or 25), 100))
    args.min_success = max(1, min(int(args.min_success or 10), args.limit))
    args.max_runtime_seconds = max(30, min(int(args.max_runtime_seconds or 900), 3600))
    args.max_pages = max(1, min(int(args.max_pages or 150), 500))
    args.max_consecutive_no_new_queries = max(1, min(int(args.max_consecutive_no_new_queries or 3), 10))
    if args.provider_test:
        return provider_test(args)
    return research(args)


if __name__ == "__main__":
    raise SystemExit(main())
