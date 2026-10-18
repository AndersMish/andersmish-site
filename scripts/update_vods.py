#!/usr/bin/env python3
"""Henter de siste VOD-ene (tidligere sendinger) for Twitch-kanalen og skriver vods.json.

Kilder (ingen nøkler/hemmeligheter):
  1. https://decapi.me/twitch/videos/<kanal>  -> VOD-ID-er + titler (DecAPI bruker Twitch Helix i bakkant)
  2. https://www.twitch.tv/videos/<id>        -> Twitchs offentlige lenkeforhåndsvisning (Open Graph-metadata):
                                                  thumbnail, varighet og dato. Valgfritt; mangler den, brukes bare ID/tittel.
  3. https://decapi.me/twitch/uptime/<kanal>  -> om kanalen er live (for å unngå unødvendige commits under en sending)

Skriver bare filen når innholdet faktisk har endret seg. DecAPI-kallet prøves på nytt
ved HTTP-feil, timeout og Twitch-rate-limit. Hvis den midlertidige feilen vedvarer,
beholdes filen og skriptet avslutter uten feilkode (med en advarsel). Andre feil
avslutter med feil og rører ikke filen.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CHANNEL = os.environ.get("TWITCH_CHANNEL", "andersmish")
LIMIT = int(os.environ.get("VOD_LIMIT", "6"))
OUT = os.environ.get("VODS_FILE", "vods.json")
UA = "andersmish-site-vod-updater/1.0 (+https://andersmish.com)"
VIDEO_URL_RE = re.compile(r"^https://www\.twitch\.tv/videos/(\d+)$")
# Tre forsøk totalt: vent 10s etter første feil, 30s etter andre.
DECAPI_ATTEMPTS = 3
DECAPI_BACKOFF_SECONDS = (10, 30)
TRANSIENT_MARKERS = ("too many requests", "error from twitch api", "try again later")


class TransientUpstreamError(Exception):
    """Midlertidig feil fra DecAPI/Twitch (rate limit, nettverk eller timeout)."""


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _parse_vod_linjer(text):
    vods = []
    for line in text.splitlines():
        url, _, title = line.partition("\t")
        m = VIDEO_URL_RE.match(url.strip())
        if not m:
            continue
        vods.append({"id": m.group(1), "title": " ".join(title.split()), "url": url.strip()})
    return vods


def _er_midlertidig_svar(text):
    low = text.lower()
    return any(marker in low for marker in TRANSIENT_MARKERS)


def _kort(verdi, lengde=240):
    tekst = " ".join(str(verdi).split())
    if len(tekst) <= lengde:
        return tekst
    return tekst[: lengde - 1] + "…"


def fetch_list():
    """Hent VOD-listen fra DecAPI. Prøver på nytt ved rate limit og nettverksfeil."""
    q = urllib.parse.urlencode({
        "limit": LIMIT,
        "broadcast_type": "archive",
        "video_format": "${url}\t${title}",
        "separator": "\n",
    })
    url = f"https://decapi.me/twitch/videos/{CHANNEL}?{q}"
    siste = "ukjent feil"
    for forsok in range(1, DECAPI_ATTEMPTS + 1):
        try:
            text = get(url).strip()
        except OSError as e:
            # HTTP-feil (inkl. 429), timeout og nettverksfeil fra urllib.
            siste = e
            midlertidig = True
        else:
            vods = _parse_vod_linjer(text)
            if vods:
                return vods
            siste = text
            midlertidig = _er_midlertidig_svar(text)
            if not midlertidig:
                raise RuntimeError(f"Ingen gyldige VOD-er i svaret fra DecAPI: {text[:200]!r}")
        if forsok < DECAPI_ATTEMPTS:
            vent = DECAPI_BACKOFF_SECONDS[min(forsok - 1, len(DECAPI_BACKOFF_SECONDS) - 1)]
            print(
                f"  ! DecAPI midlertidig feil (forsøk {forsok}/{DECAPI_ATTEMPTS}): {_kort(siste)}. "
                f"Prøver igjen om {vent}s.",
                file=sys.stderr,
            )
            time.sleep(vent)
            continue
        raise TransientUpstreamError(_kort(siste))
    raise TransientUpstreamError(_kort(siste))


def og_meta(vod_id, attempts=3):
    """Les Open Graph-metadata fra Twitchs offentlige VOD-side. Returnerer {} ved feil."""
    for attempt in range(attempts):
        try:
            page = get(f"https://www.twitch.tv/videos/{vod_id}")
            if "og:video:duration" in page:
                break
        except Exception as e:  # noqa: BLE001
            print(f"  ! metadata for {vod_id} feilet: {e}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    else:
        return {}
    meta = {}
    for tag in re.findall(r"<meta\b[^>]*>", page):
        key = re.search(r'(?:property|name)="([^"]+)"', tag)
        val = re.search(r'content="([^"]*)"', tag)
        if key and val:
            meta.setdefault(key.group(1), html.unescape(val.group(1)))
    out = {}
    thumb = meta.get("og:image", "")
    if thumb.startswith("https://static-cdn.jtvnw.net/") and "404_processing" not in thumb:
        out["thumbnail"] = thumb.replace("-640x360.", "-320x180.")
        out["thumbnail_large"] = thumb
    if meta.get("og:video:duration", "").isdigit():
        out["duration_seconds"] = int(meta["og:video:duration"])
    if re.match(r"^\d{4}-\d\d-\d\dT", meta.get("og:video:release_date", "")):
        out["created_at"] = meta["og:video:release_date"]
    return out


def is_live():
    try:
        txt = get(f"https://decapi.me/twitch/uptime/{CHANNEL}").strip().lower()
    except Exception:  # noqa: BLE001
        return None
    if "offline" in txt:
        return False
    if re.search(r"\d+\s*(second|minute|hour)", txt):
        return True
    return None  # ukjent/feilmelding


def main():
    try:
        vods = fetch_list()
    except TransientUpstreamError as e:
        # GitHub Actions viser ::warning:: uten å feile jobben. Filen skrives ikke.
        melding = (
            f"Klarte ikke hente VOD-liste fra DecAPI etter {DECAPI_ATTEMPTS} forsøk "
            f"(midlertidig feil: {e}). Beholder eksisterende {OUT}."
        )
        print(f"::warning::{melding.replace('::', ' ')}")
        return
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:  # noqa: BLE001
            old = {}
    old_vods = old.get("vods", [])
    old_by_id = {v.get("id"): v for v in old_vods}

    for v in vods:
        meta = og_meta(v["id"])
        # Behold tidligere kjente felt hvis metadata midlertidig ikke kunne hentes.
        for k, val in old_by_id.get(v["id"], {}).items():
            if k not in meta and k not in v:
                meta[k] = val
        v.update(meta)
        print(f"  {v['id']}  {v.get('created_at', '?'):22} {v.get('duration_seconds', '?'):>6}s  {v['title']}")
    live = is_live()

    def core(lst):
        return [(v.get("id"), v.get("title")) for v in lst]

    changed = core(vods) != core(old_vods)
    if not changed and live is False and vods != old_vods:
        # Varighet/thumbnail for siste sending blir endelig først etter at streamen er ferdig.
        changed = True
    if not changed:
        print(f"Ingen endring (live={live}).")
        return

    data = {
        "channel": CHANNEL,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "decapi.me/twitch/videos + Twitch Open Graph-metadata",
        "latest": vods[0],
        "vods": vods,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Skrev {OUT}: {len(vods)} VOD-er, siste = {vods[0]['id']} (live={live}).")


if __name__ == "__main__":
    main()
