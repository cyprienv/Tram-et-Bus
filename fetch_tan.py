#!/usr/bin/env python3
"""
Génère horaires.json pour le panneau Naolib (tram 2, tram 3, busway 5).

  - Temps réel   : flux GTFS-RT "trip-updates" (public, sans clé)
  - Théorique    : GTFS officiel Naolib (téléchargé, mis en cache dans horaires.json)

Aucune dépendance externe (bibliothèque standard uniquement).
Les arrêts sont retrouvés automatiquement à partir de leur NOM et de la
DESTINATION : tu n'as aucun identifiant à saisir.
"""
import csv
import hashlib
import io
import json
import os
import sys
import unicodedata
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------------
# CONFIGURATION : seul endroit à modifier pour changer d'arrêt / de ligne
# ----------------------------------------------------------------------------
CARTES = {
    "l2": {"ligne": "2", "arret": "Wattignies",    "destination": "Orvault Grand Val"},
    "l3": {"ligne": "3", "arret": "Wattignies",    "destination": "Marcel Paul"},
    "l5": {"ligne": "5", "arret": "Vincent Gâche", "destination": "Gare Sud"},
}

RT_URL = os.environ.get(
    "GTFS_RT_URL",
    "https://proxy.transport.data.gouv.fr/resource/naolib-nantes-gtfs-rt-trip-update",
)
DATASET_API = "https://transport.data.gouv.fr/api/datasets/reseau-de-transports-collectifs-naolib"
GTFS_FALLBACK_URL = (
    "https://data.nantesmetropole.fr/explore/dataset/"
    "244400404_transports_commun_naolib_nantes_metropole_gtfs/files/"
    "0cc0469a72de54ee045cb66d1a21de9e/download/"
)
OUTPUT = os.environ.get("OUTPUT", "horaires.json")
PARIS = ZoneInfo("Europe/Paris")
NB_DEPARTS = 8                 # départs conservés par carte
CACHE_MAX_AGE = 6 * 3600       # on retélécharge le GTFS au plus toutes les 6 h
CACHE_MIN_HORIZON = 3 * 3600   # ... ou si le cache couvre moins de 3 h devant nous

errors = []


def warn(msg):
    errors.append(msg)
    print(f"::warning::{msg}")


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def tail(x):
    """'FR_NAOLIB:VehicleJourney:123-abc' -> '123-abc' (compare GTFS et RT)."""
    return (x or "").split(":")[-1]


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "tram-et-bus/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ----------------------------------------------------------------------------
# Décodeur protobuf minimal pour GTFS-RT (évite d'installer des paquets)
# ----------------------------------------------------------------------------
def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return r, i


def _msg(b):
    out, i = {}, 0
    while i < len(b):
        k, i = _varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {w} inconnu")
        out.setdefault(f, []).append(v)
    return out


def _signed(x):
    return x - (1 << 64) if x >= 1 << 63 else x


def _txt(v):
    return v.decode("utf-8", "replace")


def parse_gtfs_rt(raw):
    """Retourne (timestamp_flux, mises_a_jour, trajets_annules, trajets_vus).

    mises_a_jour : liste de (ligne, quai, heure_epoch, retard_s|None, trip_id)
    """
    top = _msg(raw)
    feed_ts = _msg(top[1][0]).get(3, [None])[0] if 1 in top else None
    updates, cancelled, seen = [], set(), set()
    for ent in top.get(2, []):
        e = _msg(ent)
        if 3 not in e:
            continue
        tu = _msg(e[3][0])
        trip = _msg(tu[1][0]) if 1 in tu else {}
        line = tail(_txt(trip[5][0])) if 5 in trip else None
        tid = _txt(trip[1][0]) if 1 in trip else ""
        if line is None:
            continue
        seen.add((line, tail(tid)))
        if trip.get(4, [0])[0] == 3:          # CANCELED
            cancelled.add((line, tail(tid)))
            continue
        for st in tu.get(2, []):
            s = _msg(st)
            if 4 not in s or s.get(5, [0])[0] == 1:   # sans arrêt / SKIPPED
                continue
            ev = None
            for fld in (3, 2):                        # départ de préférence
                if fld in s:
                    ev = _msg(s[fld][0])
                    break
            if not ev or 2 not in ev:
                continue
            delay = _signed(ev[1][0]) if 1 in ev else None
            updates.append((line, _txt(s[4][0]), ev[2][0], delay, tid))
    return feed_ts, updates, cancelled, seen


# ----------------------------------------------------------------------------
# GTFS théorique
# ----------------------------------------------------------------------------
def find_gtfs_url():
    try:
        meta = json.loads(http_get(DATASET_API, 30))
        for res in meta.get("resources", []):
            if (res.get("format") or "").upper() == "GTFS" and res.get("url"):
                return res["url"]
    except Exception as exc:  # noqa: BLE001
        warn(f"API transport.data.gouv.fr indisponible ({exc}); URL GTFS de secours utilisée")
    return GTFS_FALLBACK_URL


def _rows(z, name):
    with z.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, "utf-8-sig", newline=""))


def _secs(hms):
    h, m, s = (hms.strip().split(":") + ["0", "0"])[:3]
    return int(h) * 3600 + int(m) * 60 + int(s)


def _service_epoch(d, secs):
    # GTFS : les heures comptent depuis "midi moins 12 h" du jour de service
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=PARIS).timestamp()
    return int(noon - 43200 + secs)


def build_theorique(now):
    path = os.environ.get("GTFS_PATH")
    if path:
        data = open(path, "rb").read()
        print(f"GTFS local : {path}")
    else:
        url = find_gtfs_url()
        print(f"Téléchargement GTFS : {url}")
        data = http_get(url, 180)
    z = zipfile.ZipFile(io.BytesIO(data))

    # lignes cibles
    wanted_lines = {c["ligne"] for c in CARTES.values()}
    route_line = {}
    for r in _rows(z, "routes.txt"):
        for cand in (r.get("route_short_name", ""), tail(r["route_id"])):
            if cand in wanted_lines:
                route_line[r["route_id"]] = cand
                break

    trips = {}
    for t in _rows(z, "trips.txt"):
        if t["route_id"] in route_line:
            trips[t["trip_id"]] = (route_line[t["route_id"]], t["service_id"],
                                   t.get("trip_headsign", ""))

    # arrêts candidats par carte (nom exact en priorité, sinon "contient")
    stop_name = {s["stop_id"]: s["stop_name"] for s in _rows(z, "stops.txt")}
    cand = {}
    for key, c in CARTES.items():
        q = norm(c["arret"])
        exact = {i for i, n in stop_name.items() if norm(n) == q}
        cand[key] = exact or {i for i, n in stop_name.items() if q in norm(n)}
        if not cand[key]:
            raise RuntimeError(f"Aucun arrêt nommé « {c['arret']} » dans le GTFS")
    all_cand = set().union(*cand.values())

    # passages aux arrêts candidats + terminus de chaque trajet
    at, last = {}, {}
    for st in _rows(z, "stop_times.txt"):
        tid = st["trip_id"]
        if tid not in trips:
            continue
        seq = int(st["stop_sequence"])
        if tid not in last or seq > last[tid][0]:
            last[tid] = (seq, st["stop_id"])
        if st["stop_id"] in all_cand:
            hms = st.get("departure_time") or st.get("arrival_time")
            if hms:
                at.setdefault(tid, []).append((st["stop_id"], _secs(hms)))

    # calendriers
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    cal = {}
    if "calendar.txt" in z.namelist():
        for r in _rows(z, "calendar.txt"):
            cal[r["service_id"]] = r
    exc = {}
    if "calendar_dates.txt" in z.namelist():
        for r in _rows(z, "calendar_dates.txt"):
            exc[(r["service_id"], r["date"])] = r["exception_type"]

    def active(service_id, d):
        ds = d.strftime("%Y%m%d")
        e = exc.get((service_id, ds))
        if e == "1":
            return True
        if e == "2":
            return False
        r = cal.get(service_id)
        return bool(r and r["start_date"] <= ds <= r["end_date"] and r[weekdays[d.weekday()]] == "1")

    today = datetime.fromtimestamp(now, PARIS).date()
    days = [today + timedelta(days=o) for o in (-1, 0, 1)]

    result = {}
    for key, c in CARTES.items():
        qd = norm(c["destination"])
        quays, deps, seen_dest = set(), [], set()
        for tid, (line, service, head) in trips.items():
            if line != c["ligne"] or tid not in at:
                continue
            term = stop_name.get(last[tid][1], "")
            seen_dest.add(f"{head} -> {term}")
            if qd not in norm(head) and qd not in norm(term):
                continue
            for stop_id, secs in at[tid]:
                if stop_id not in cand[key]:
                    continue
                quays.add(stop_id)
                for d in days:
                    if active(service, d):
                        e = _service_epoch(d, secs)
                        if now - 3600 <= e <= now + 30 * 3600:
                            deps.append([e, tid])
        if not quays:
            raise RuntimeError(
                f"[{key}] aucun trajet ligne {c['ligne']} vers « {c['destination']} » "
                f"à « {c['arret']} ». Directions connues : {sorted(seen_dest)[:12]}")
        deps.sort()
        result[key] = {"quais": sorted(quays), "departs": deps}
        print(f"[{key}] quais={sorted(quays)} départs théoriques={len(deps)}")
    return result


# ----------------------------------------------------------------------------
# Fusion théorique + temps réel
# ----------------------------------------------------------------------------
def merge(line, th, rt_updates, cancelled, seen, quays, now):
    rts = [(t, d, tid) for (ln, q, t, d, tid) in rt_updates if ln == line and q in quays]
    used, out = set(), []
    by_trip = {tail(tid): i for i, (_, tid) in enumerate(th)}
    for t, delay, tid in rts:
        idx = by_trip.get(tail(tid))
        if idx is None and delay is not None:
            sched = t - delay
            near = [(abs(th[i][0] - sched), i) for i in range(len(th))
                    if i not in used and abs(th[i][0] - sched) <= 90]
            idx = min(near)[1] if near else None
        if idx is not None:
            used.add(idx)
        out.append({"t": t, "rt": True, "retard": delay})
    for i, (e, tid) in enumerate(th):
        if i in used:
            continue
        k = (line, tail(tid))
        if k in cancelled or k in seen:   # annulé, ou déjà suivi en temps réel ailleurs
            continue
        out.append({"t": e, "rt": False})
    out = [o for o in out if o["t"] >= now - 60]
    out.sort(key=lambda o: o["t"])
    return out[:NB_DEPARTS]


# ----------------------------------------------------------------------------
def main():
    now = int(os.environ.get("NOW_EPOCH") or datetime.now(PARIS).timestamp())
    sig = hashlib.sha1(json.dumps(CARTES, sort_keys=True).encode()).hexdigest()[:12]

    old = {}
    if os.path.exists(OUTPUT):
        try:
            old = json.load(open(OUTPUT, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}

    # --- théorique (cache) ---
    cache = old.get("theorique")
    fresh = (
        cache and old.get("theorique_cfg") == sig
        and now - old.get("theorique_built", 0) < CACHE_MAX_AGE
        and all(v["departs"] and v["departs"][-1][0] > now + CACHE_MIN_HORIZON
                for v in cache.values())
    )
    built = old.get("theorique_built", 0)
    if not fresh:
        try:
            cache = build_theorique(now)
            built = now
        except Exception as exc:  # noqa: BLE001
            if not cache or old.get("theorique_cfg") != sig:
                raise  # rien d'exploitable : on arrête (le workflow sera rouge)
            warn(f"GTFS non mis à jour ({exc}); ancien cache conservé")
    else:
        print("Cache théorique encore valable")

    # --- temps réel ---
    updates, cancelled, seen, feed_ts = [], set(), set(), None
    try:
        if os.environ.get("RT_PATH"):
            raw = open(os.environ["RT_PATH"], "rb").read()
        else:
            raw = http_get(RT_URL, 30)
        feed_ts, updates, cancelled, seen = parse_gtfs_rt(raw)
        print(f"GTFS-RT : {len(updates)} passages, flux de {feed_ts}")
    except Exception as exc:  # noqa: BLE001
        warn(f"Temps réel indisponible ({exc}) : horaires théoriques uniquement")

    data = {}
    for key, c in CARTES.items():
        info = cache[key]
        data[key] = merge(c["ligne"], info["departs"], updates, cancelled, seen,
                          set(info["quais"]), now)
        nrt = sum(1 for d in data[key] if d["rt"])
        print(f"[{key}] {len(data[key])} départs dont {nrt} en temps réel")

    payload = {
        "updated_at": datetime.fromtimestamp(now, PARIS).isoformat(),
        "updated_epoch": now,
        "rt_feed_epoch": feed_ts,
        "rt_ok": feed_ts is not None,
        "errors": errors,
        "data": data,
        "theorique": cache,
        "theorique_built": built,
        "theorique_cfg": sig,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{OUTPUT} écrit.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"::error::{exc}")
        sys.exit(1)
