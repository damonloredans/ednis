"""EDNIS control panel.

The whole UI lives in ui.html (HTML/CSS/JS). This module opens it in a native
always-on-top window (pywebview) and exposes `Api` to the page. All NetSuite /
eDesk work still lives in the *_bridge modules and runs on background threads;
progress is pushed back to the page via evaluate_js.
"""

import json
import os
import threading

import webview

from edesk_bridge import detect_ecom_number, detect_order_numbers, open_tracking_page
from netsuite_bridge import (
    open_ecom_record,
    open_return_auths,
    open_sales_orders,
    open_sales_orders_with_ra,
)
from order_parser import parse_order_number

HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
WIN_W = 300


class Api:
    """Exposed to the page as `window.pywebview.api`. Only public (non-`_`)
    methods are reachable from JS: open_selected, search_manual, resolve_pick."""

    def __init__(self):
        # NOTE: everything here is underscore-prefixed on purpose. pywebview
        # exposes every *public* attribute of the js_api object to the page and
        # tries to serialize it — handing it the native window object sends it
        # into infinite recursion.
        self._window = None
        self._pick_event = None
        self._pick_result = None

    def _bind(self, window):
        self._window = window

    # --- Python -> page -------------------------------------------------------

    def _js(self, fn, *args):
        payload = ", ".join(json.dumps(a) for a in args)
        self._window.evaluate_js(f"window.app.{fn}({payload})")

    def _status(self, text, state="info"):
        self._js("setStatus", text, state)

    def _busy(self, is_busy):
        self._js("setBusy", is_busy)

    def _pick_page(self, labels, allow_all=False):
        """Blocks the worker thread until the user picks a ticket in the page's
        overlay (or cancels). Returns an index, or "all" when `allow_all` and
        the user chose "open all", or raises on cancel."""
        self._pick_event = threading.Event()
        self._pick_result = None
        self._js("showPicker", labels, allow_all)
        self._pick_event.wait()
        if self._pick_result is None:
            raise RuntimeError("Cancelled — no ticket selected.")
        return self._pick_result

    # --- page -> Python -----------------------------------------------------

    def resolve_pick(self, index):
        self._pick_result = index
        if self._pick_event:
            self._pick_event.set()

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()

    def fit(self, height):
        """Called from the page once it knows its rendered height, so the
        frameless window hugs the content instead of leaving dead space."""
        try:
            self._window.resize(WIN_W, max(200, int(round(height))))
        except Exception:
            pass

    def open_selected(self, sel):
        threading.Thread(target=self._run_open, args=(sel or {},), daemon=True).start()

    def search_manual(self, raw, want_so, want_ra):
        raw = (raw or "").strip()
        if not raw:
            self._status("Paste an order number first.", "error")
            return
        query = parse_order_number(raw)
        if not query:
            self._status(f"Couldn't find an order number in: {raw[:60]}", "error")
            return
        if not want_so and not want_ra:
            want_so = True  # manual box is Sales Order / RA; default to SO
        threading.Thread(
            target=self._run_manual, args=(query, want_so, want_ra), daemon=True
        ).start()

    # --- workers -----------------------------------------------------------

    def _run_open(self, sel):
        self._busy(True)
        try:
            results = []
            if sel.get("so") or sel.get("ra"):
                results.append(self._open_order(sel.get("so"), sel.get("ra")))
            if sel.get("tracking"):
                results.append(self._open_tracking())
            if sel.get("ecom"):
                results.append(self._open_ecom())

            if not results:
                self._status("Pick at least one thing to open first.", "error")
            else:
                self._status("Done — " + "; ".join(results), "success")
        except Exception as e:
            self._status(str(e), "error")
        finally:
            self._busy(False)

    def _run_manual(self, query, want_so, want_ra):
        self._busy(True)
        try:
            summary = self._open_order_records(query, want_so, want_ra)
            self._status(f"{query} — {summary}.", "success")
            self._js("clearEntry")
        except Exception as e:
            self._status(str(e), "error")
        finally:
            self._busy(False)

    # --- shared steps ----------------------------------------------------

    def _open_order(self, want_so, want_ra):
        queries = detect_order_numbers(log=self._status, pick_page=self._pick_page)
        if not queries:
            return "no order number found"
        if len(queries) == 1:
            return self._open_order_records(queries[0], want_so, want_ra)

        # "open all" across multiple eDesk tickets — one NetSuite pass each
        parts = []
        for q in queries:
            try:
                parts.append(f"{q}: {self._open_order_records(q, want_so, want_ra)}")
            except Exception as e:
                parts.append(f"{q}: {e}")
        return f"{len(queries)} tickets — " + " | ".join(parts)

    def _open_order_records(self, query, want_so, want_ra):
        """Opens exactly what's asked for: Sales Order, Return Authorization,
        or both. Returns a short summary string for the status line."""
        if want_so and want_ra:
            so_count, ra_count = open_sales_orders_with_ra(query, log=self._status)
            so_noun = "sales order" if so_count == 1 else "sales orders"
            if ra_count == 0:
                return f"{so_count} {so_noun}, no RA tied"
            ra_noun = "RA" if ra_count == 1 else "RAs"
            return f"{so_count} {so_noun} + {ra_count} {ra_noun}"

        if want_ra:
            ra_count = open_return_auths(query, log=self._status)
            if ra_count == 0:
                return "no RA tied to this order"
            return f"{ra_count} RA" if ra_count == 1 else f"{ra_count} RAs"

        count = open_sales_orders(query, log=self._status)
        noun = "sales order" if count == 1 else "sales orders"
        return f"{count} {noun}"

    def _open_tracking(self):
        carrier, number = open_tracking_page(log=self._status, pick_page=self._pick_page)
        return f"{carrier} tracking" if number else "no tracking number found"

    def _open_ecom(self):
        number = detect_ecom_number(log=self._status, pick_page=self._pick_page)
        if not number:
            return "no ecom record number found"
        open_ecom_record(number, log=self._status)
        return "ecom record + parent"


def main():
    api = Api()
    window = webview.create_window(
        "EDNIS",
        HTML_FILE,
        js_api=api,
        width=WIN_W,
        height=360,
        min_size=(WIN_W, 200),
        frameless=True,
        on_top=True,
        background_color="#0e2b2b",
    )
    api._bind(window)
    webview.start()


if __name__ == "__main__":
    main()
