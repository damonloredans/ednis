"""Reads the order number and tracking info off the currently open eDesk
ticket tab in the automation Chrome window. Uses the same CDP connection
approach as netsuite_bridge.py.
"""

import re

from playwright.sync_api import sync_playwright

from chrome_utils import open_background_tab
from order_parser import parse_order_number

CDP_URL = "http://localhost:9222"

TRACKING_URL_TEMPLATES = {
    "USPS": "https://tools.usps.com/tracking/{}",
    "UPS": "https://www.ups.com/track?track=yes&trackNums={}",
    "AMAZON": "https://track.amazon.com/tracking/{}",
    "FEDEX": "https://www.fedex.com/wtrk/track/?trknbr={}",
    "DHL": "https://www.dhl.com/ph-en/home/tracking.html?tracking-id={}",
}

# 8+ chars of letters/digits. A real tracking number always has at least one
# digit — this lets us skip service-name words ("PRIORITY", "ECOMMERCE",
# "GROUND") that sit between the carrier keyword and the actual number.
TRACKING_TOKEN_RE = re.compile(r"[A-Z0-9]{8,}")


def _first_tracking_number(upper: str, start: int = 0):
    for m in TRACKING_TOKEN_RE.finditer(upper, start):
        token = m.group(0)
        if any(c.isdigit() for c in token):
            return token
    return None

# Loose keyword match, not an exact carrier-name match: if any of these
# substrings shows up in the text next to the TRACKING NO. label, that's the
# carrier. Order matters — more specific / less ambiguous keys are checked
# first, so "USPS" wins before the bare "UPS" fallback, and a full
# "DHL eCommerce America" still resolves to DHL.
CARRIER_KEYWORDS = {
    "USPS": ("USPS", "U.S.P.S", "UNITED STATES POSTAL", "POSTAL SERVICE", "US MAIL"),
    "FEDEX": ("FEDEX", "FED EX", "FED-EX"),
    "DHL": ("DHL",),
    "AMAZON": ("AMAZON SHIPPING", "AMAZON LOGISTICS", "AMZL"),
    "UPS": ("UPS", "UNITED PARCEL"),
}

ECOM_NUMBER_RE = re.compile(r"#\s*(\d+)")

# Individual ticket pages look like dashboard-3.edesk.com/crm/view/715417337 —
# this excludes list/inbox views like /crm/new or /crm/todo, which aren't
# tickets and shouldn't be offered as a pick.
TICKET_URL_RE = re.compile(r"edesk\.com/crm/view/\d+")


def _find_edesk_pages(context):
    return [p for p in context.pages if TICKET_URL_RE.search(p.url)]


class NoTicketTabsError(RuntimeError):
    pass


def _match_carrier(upper: str):
    """First (carrier, index-just-past-the-keyword) whose keyword appears in
    `upper`, or (None, -1). Loose substring match, not an exact name match."""
    for carrier, keywords in CARRIER_KEYWORDS.items():
        for kw in keywords:
            idx = upper.find(kw)
            if idx != -1:
                return carrier, idx + len(kw)
    return None, -1


def parse_tracking_info(text: str):
    """Returns (carrier, tracking_number, url) or (None, None, None).

    The carrier is matched loosely: any known keyword anywhere in the text is
    enough ('USPS Priority Mail', 'FedEx Ground Economy', 'DHL eCommerce'),
    not an exact carrier-name string. Once a carrier is recognized, its
    specific tracking URL is always used. Prefers a tracking number that comes
    after the carrier mention, but falls back to the first one anywhere."""
    upper = text.upper()
    carrier, after_idx = _match_carrier(upper)
    if not carrier:
        return None, None, None

    number = _first_tracking_number(upper, after_idx) or _first_tracking_number(upper)
    if not number:
        return None, None, None

    url = TRACKING_URL_TEMPLATES[carrier].format(number)
    return carrier, number, url


def _read_order_number(page):
    """Reads the value next to the ORDER NO. label. Deliberately does NOT
    fall back to scanning the whole page — that previously picked up
    unrelated '#'-prefixed text elsewhere (e.g. a product SKU) and mistook
    it for the order number. Missing it and prompting for manual entry is
    much safer than silently grabbing the wrong one."""
    label = page.get_by_text("ORDER NO.", exact=False)
    if label.count() == 0:
        return None
    for xpath in ("xpath=ancestor::div[1]", "xpath=ancestor::div[2]", "xpath=ancestor::div[3]"):
        try:
            container_text = label.first.locator(xpath).inner_text(timeout=1000)
        except Exception:
            continue
        query = parse_order_number(container_text)
        if query:
            return query
    return None


def _read_ecom_number(page):
    try:
        page_text = page.inner_text("body")
    except Exception:
        return None
    m = ECOM_NUMBER_RE.search(page_text)
    return m.group(1) if m else None


def _read_tracking_info(page):
    """Reads the value next to the TRACKING NO. label. Same reasoning as
    _read_order_number: no whole-page fallback, to avoid matching an
    unrelated carrier-name mention elsewhere on the ticket."""
    label = page.get_by_text("TRACKING NO.", exact=False)
    if label.count() == 0:
        return None, None, None
    for xpath in ("xpath=ancestor::div[1]", "xpath=ancestor::div[2]", "xpath=ancestor::div[3]"):
        try:
            container_text = label.first.locator(xpath).inner_text(timeout=1000)
        except Exception:
            continue
        carrier, number, url = parse_tracking_info(container_text)
        if url:
            return carrier, number, url
    return None, None, None


def _order_hint(page):
    order = _read_order_number(page)
    if order:
        return order
    ecom = _read_ecom_number(page)
    return f"#{ecom}" if ecom else ""


def _tracking_hint(page):
    carrier, number, _url = _read_tracking_info(page)
    return f"{carrier} {number}" if number else ""


def _page_label(page, hint_fn=None):
    """Best-effort human label for a ticket tab: a relevant hint (order
    number, tracking number, ...) plus the ticket ID from the URL, so picking
    the right one is obvious."""
    m = re.search(r"/view/(\d+)", page.url)
    ticket_id = m.group(1) if m else "?"

    try:
        hint = (hint_fn or _order_hint)(page)
    except Exception:
        hint = ""

    return f"Ticket {ticket_id} — {hint}" if hint else f"Ticket {ticket_id}"


def _resolve_edesk_page(context, pick_page, qualifies=None, label_hint=None):
    """Finds the eDesk ticket tab to read. `qualifies(page)` (if given)
    filters to tabs that actually have the thing we're looking for — if none
    qualify, returns None without prompting. If more than one qualifies,
    asks `pick_page(labels)` (when given) which one to use."""
    all_candidates = _find_edesk_pages(context)
    if not all_candidates:
        raise NoTicketTabsError(
            "No eDesk tab found in the automation Chrome window. Open the "
            "ticket there first."
        )

    candidates = [p for p in all_candidates if qualifies(p)] if qualifies else all_candidates
    if not candidates:
        return None
    if len(candidates) == 1 or pick_page is None:
        return candidates[0]

    labels = [_page_label(p, label_hint) for p in candidates]
    idx = pick_page(labels)
    if idx is None:
        raise RuntimeError("Cancelled — no ticket selected.")
    return candidates[idx]


def detect_order_number(log=print, pick_page=None) -> str | None:
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise RuntimeError(
                "Can't reach Chrome on port 9222. Run launch_chrome_debug.bat "
                "first and make sure a ticket is open in that window."
            ) from e

        context = browser.contexts[0]
        page = _resolve_edesk_page(
            context, pick_page, qualifies=lambda pg: _read_order_number(pg) is not None
        )
        if page is None:
            log("No open ticket has an ORDER NO. field (probably pre-sales).")
            return None

        log(f"Reading order number from: {page.url}")
        return _read_order_number(page)


def detect_order_numbers(log=print, pick_page=None) -> list[str]:
    """Like detect_order_number, but the ticket picker also offers "open all" —
    so several eDesk tickets can be pushed to NetSuite in one go. Returns the
    NetSuite search queries (deduped, order preserved); may be empty.

    `pick_page` is called as `pick_page(labels, allow_all=True)` and may return
    an index, the string "all", or None (cancelled)."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise RuntimeError(
                "Can't reach Chrome on port 9222. Run launch_chrome_debug.bat "
                "first and make sure a ticket is open in that window."
            ) from e

        context = browser.contexts[0]
        if not _find_edesk_pages(context):
            raise NoTicketTabsError(
                "No eDesk tab found in the automation Chrome window. Open the "
                "ticket there first."
            )

        candidates = [
            pg for pg in _find_edesk_pages(context)
            if _read_order_number(pg) is not None
        ]
        if not candidates:
            log("No open ticket has an ORDER NO. field (probably pre-sales).")
            return []

        if len(candidates) == 1 or pick_page is None:
            chosen = [candidates[0]]
        else:
            labels = [_page_label(pg) for pg in candidates]
            picked = pick_page(labels, allow_all=True)
            if picked is None:
                raise RuntimeError("Cancelled — no ticket selected.")
            chosen = candidates if picked == "all" else [candidates[picked]]

        queries, seen = [], set()
        for pg in chosen:
            log(f"Reading order number from: {pg.url}")
            q = _read_order_number(pg)
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        return queries


def detect_ecom_number(log=print, pick_page=None) -> str | None:
    """Reads the number after '#' in a pre-sales ticket's subject line (there's
    no ORDER NO. field yet on these — just an ecom record number)."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise RuntimeError(
                "Can't reach Chrome on port 9222. Run launch_chrome_debug.bat "
                "first and make sure a ticket is open in that window."
            ) from e

        context = browser.contexts[0]
        page = _resolve_edesk_page(
            context, pick_page, qualifies=lambda pg: _read_ecom_number(pg) is not None
        )
        if page is None:
            log("No open ticket has an ecom record number.")
            return None

        log(f"Reading ecom record number from: {page.url}")
        return _read_ecom_number(page)


def open_tracking_page(log=print, pick_page=None):
    """Detects the tracking carrier + number on the current eDesk ticket and
    opens the carrier's tracking page in a new tab. Returns (carrier, number)
    or (None, None) if nothing was found."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise RuntimeError(
                "Can't reach Chrome on port 9222. Run launch_chrome_debug.bat "
                "first and make sure a ticket is open in that window."
            ) from e

        context = browser.contexts[0]
        page = _resolve_edesk_page(
            context,
            pick_page,
            qualifies=lambda pg: _read_tracking_info(pg)[2] is not None,
            label_hint=_tracking_hint,
        )
        if page is None:
            return None, None

        log(f"Reading tracking info from: {page.url}")
        carrier, number, url = _read_tracking_info(page)
        if not url:
            return None, None

        log(f"Found {carrier} tracking number {number}, opening...")
        open_background_tab(context, url)
        return carrier, number
