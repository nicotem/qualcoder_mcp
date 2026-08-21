# Folded into the main suite from the v0.6.0-alpha parallel test campaign
# (track 6, scale & media); adapted paths/fixtures only — test logic unchanged.
"""Track 6 project builders: large-scale and media-heavy valid v14 projects.

These builders emit QualCoder's *real* v14 DDL (copied verbatim from
qualcoder/__main__.py create-project path), so timings/behaviour reflect a
project a researcher would actually open in QualCoder 3.8 — in particular the
absence of any secondary index on code_text.fid / case_text.fid, which is the
scaling risk the scale probes target.

Nothing here touches ~/Documents/QDA Projects or the MCP default workspace.
"""

import os
import sqlite3
from pathlib import Path

QUALCODER_VERSION = "QualCoder 3.8 (track6 synthetic)"

# ---- QualCoder's real v14 CREATE TABLE statements (subset the server reads) ---
# Order/columns match qualcoder/__main__.py:2348-2417. The server reads by
# column name, so only names + constraints matter for fidelity.
DDL = [
    "CREATE TABLE project (databaseversion text, date text, memo text, about text, "
    "bookmarkfile integer, bookmarkpos integer, codername text, recently_used_codes text)",
    "CREATE TABLE source (id integer primary key, name text, fulltext text, mediapath text, "
    "memo text, owner text, date text, av_text_id integer, risid integer, unique(name))",
    "CREATE TABLE code_image (imid integer primary key,id integer,x1 integer, y1 integer, "
    "width integer, height integer, cid integer, memo text, date text, owner text, "
    "important integer, pdf_page integer)",
    "CREATE TABLE code_av (avid integer primary key,id integer,pos0 integer, pos1 integer, "
    "cid integer, memo text, date text, owner text, important integer)",
    "CREATE TABLE annotation (anid integer primary key, fid integer,pos0 integer, pos1 integer, "
    "memo text, owner text, date text, unique(fid,pos0,pos1,owner))",
    "CREATE TABLE attribute_type (name text primary key, date text, owner text, memo text, "
    "caseOrFile text, valuetype text)",
    "CREATE TABLE attribute (attrid integer primary key, name text, attr_type text, value text, "
    "id integer, date text, owner text, unique(name,attr_type,id))",
    "CREATE TABLE case_text (id integer primary key, caseid integer, fid integer, pos0 integer, "
    "pos1 integer, owner text, date text, memo text)",
    "CREATE TABLE cases (caseid integer primary key, name text, memo text, owner text,date text, "
    "constraint ucm unique(name))",
    "CREATE TABLE code_cat (catid integer primary key, name text, owner text, date text, memo text, "
    "supercatid integer, unique(name))",
    "CREATE TABLE code_text (ctid integer primary key, cid integer, fid integer,seltext text, "
    "pos0 integer, pos1 integer, owner text, date text, memo text, avid integer, important integer, "
    "unique(cid,fid,pos0,pos1, owner))",
    "CREATE TABLE code_name (cid integer primary key, name text, memo text, catid integer, "
    "owner text,date text, color text, unique(name))",
    "CREATE TABLE journal (jid integer primary key, name text, jentry text, date text, owner text, "
    "unique(name))",
    "CREATE TABLE coder_names (name TEXT UNIQUE NOT NULL, visibility INTEGER NOT NULL DEFAULT 1 CHECK (visibility IN (0, 1)))",
    "CREATE TABLE stored_sql (title text, description text, grouper text, ssql text, unique(title))",
]

OWNER = "researcher1"
DATE = "2024-01-15 10:00:00"
COLORS = ["#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
          "#f032e6", "#bfef45", "#fabed4", "#469990"]


def _connect(project_folder: Path) -> sqlite3.Connection:
    project_folder.mkdir(parents=True, exist_ok=True)
    db_file = project_folder / "data.qda"
    if db_file.exists():
        db_file.unlink()
    conn = sqlite3.connect(str(db_file))
    for stmt in DDL:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO project (databaseversion, date, memo, about, codername) "
        "VALUES ('v14', ?, ?, ?, ?)",
        (DATE, "track6 synthetic project", QUALCODER_VERSION, OWNER),
    )
    return conn


def _para(seed: int, n_sentences: int = 6) -> str:
    """Deterministic, position-safe (ASCII, \\n only) paragraph text."""
    vocab = ["stress", "deadline", "coping", "support", "workload", "burnout",
             "resilience", "manager", "overtime", "recovery", "anxiety",
             "balance", "pressure", "colleague", "deadline", "wellbeing"]
    out = []
    for s in range(n_sentences):
        words = [vocab[(seed + s + w) % len(vocab)] for w in range(8)]
        out.append("The " + " ".join(words) + " matters here.")
    return " ".join(out)


def build_scale_project(
    project_folder: Path,
    n_files: int = 320,
    n_codes: int = 60,
    n_categories: int = 45,
    n_cases: int = 55,
    target_codings: int = 12000,
    big_doc_chars: int = 500_000,
    n_hot_files: int = 3,
    mega_code_codings: int = 0,
) -> dict:
    """Build a large valid v14 project. Returns a stats dict.

    - A deep category tree (chained supercatid) of n_categories nodes.
    - n_codes codes distributed across the leaf categories.
    - n_files text sources; one is `big_doc_chars` long.
    - `n_hot_files` files concentrate a large share of codings (to stress the
      code_text self-join in co-occurrence and the case-code matrix).
    - target_codings total code_text rows (unique per cid,fid,pos0,pos1,owner).
    - n_cases, each whole-file-linked to several files via case_text.
    - attributes on cases + files for query_by_attribute.
    """
    conn = _connect(project_folder)
    cur = conn.cursor()

    # ---- deep category tree: node i's parent is (i-1)//2 -> chains up to ~log,
    # but we also build one long linear chain to guarantee real depth ----
    cats = []
    for i in range(1, n_categories + 1):
        if i <= 8:
            supercat = None if i == 1 else i - 1          # long linear chain (depth 8)
        else:
            supercat = ((i - 9) % 8) + 1                   # remaining hang off the chain
        cats.append((i, f"Category {i:03d}", OWNER, DATE, f"memo for cat {i}", supercat))
    cur.executemany(
        "INSERT INTO code_cat (catid,name,owner,date,memo,supercatid) VALUES (?,?,?,?,?,?)",
        cats,
    )

    # ---- codes across categories ----
    codes = []
    for c in range(1, n_codes + 1):
        catid = ((c - 1) % n_categories) + 1
        codes.append((c, f"Code {c:03d}", f"memo for code {c}", catid, OWNER, DATE,
                      COLORS[c % len(COLORS)]))
    cur.executemany(
        "INSERT INTO code_name (cid,name,memo,catid,owner,date,color) VALUES (?,?,?,?,?,?,?)",
        codes,
    )

    # ---- source files (all text; one huge; hot files are large) ----
    files = []
    big_doc_fid = 1
    hot_files = list(range(2, 2 + n_hot_files))  # large files that concentrate codings

    def _pad(seed, target):
        text = _para(seed, 4)
        while len(text) < target:
            text += " " + _para(seed + len(text), 4)
        return text[:target]

    for f in range(1, n_files + 1):
        if f == big_doc_fid:
            text = _pad(f, big_doc_chars)
        elif f in hot_files:
            text = _pad(f, 40_000)        # big enough to hold thousands of codings
        else:
            text = _para(f, 40)           # ~2 KB each
        files.append((f, f"interview_{f:04d}.txt", text, None, f"memo file {f}",
                      OWNER, DATE, None, None))
    cur.executemany(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date,av_text_id,risid) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        files,
    )
    file_texts = {fid: t for (fid, _n, t, *_r) in files}

    # ---- codings ----
    # Distribute target_codings: hot files get a big concentrated share; the
    # rest spread across the remaining files. Positions are non-overlapping
    # slices so (cid,fid,pos0,pos1,owner) stays unique.
    coding_rows = []
    ctid = 1

    def add_coding(cid, fid, pos0, pos1):
        nonlocal ctid
        seltext = file_texts[fid][pos0:pos1]
        coding_rows.append((ctid, cid, fid, seltext, pos0, pos1, OWNER, DATE,
                            "", None, 1 if ctid % 7 == 0 else 0))
        ctid += 1

    hot_share = int(target_codings * 0.55)
    per_hot = max(1, hot_share // max(1, n_hot_files))
    # In a hot file, codings OVERLAP heavily (stride < seg_len) and cycle a
    # small code set, so find_code_cooccurrences(window=0) has real partners
    # and the code_text self-join on fid does per_hot*(per_hot/COOC_CODES)
    # comparisons per hot file (the O(n^2) probe).
    COOC_CODES = 6
    for hf in hot_files:
        text = file_texts[hf]
        seg_len, stride = 40, 8
        max_segs = max(0, (len(text) - seg_len) // stride)
        n = min(per_hot, max_segs)
        for k in range(n):
            pos0 = k * stride
            pos1 = pos0 + seg_len
            cid = (k % COOC_CODES) + 1
            add_coding(cid, hf, pos0, pos1)

    # Big document gets a decent chunk of codings (memory probe on 500k doc).
    big_text = file_texts[big_doc_fid]
    big_target = int(target_codings * 0.10)
    seg_len = 20
    for k in range(big_target):
        pos0 = k * 40
        pos1 = pos0 + seg_len
        if pos1 > len(big_text):
            break
        cid = (k % n_codes) + 1
        add_coding(cid, big_doc_fid, pos0, pos1)

    # Spread the remainder across ordinary files.
    remaining = target_codings - len(coding_rows)
    ordinary = [f for f in range(2 + n_hot_files, n_files + 1)]
    fi = 0
    while remaining > 0 and ordinary:
        fid = ordinary[fi % len(ordinary)]
        text = file_texts[fid]
        # up to a handful per ordinary file per pass
        pass_idx = fi // len(ordinary)
        seg_len = 10
        pos0 = pass_idx * 12
        pos1 = pos0 + seg_len
        if pos1 <= len(text):
            cid = (fi % n_codes) + 1
            # guard uniqueness: (cid,fid,pos0) unique within our scheme
            add_coding(cid, fid, pos0, pos1)
            remaining -= 1
        fi += 1
        if fi > len(ordinary) * 400:  # safety valve
            break

    # Optional mega-code to exercise REFI 5000/code truncation disclosure.
    # Placed in the big document (plenty of room), at high positions that do
    # not collide with the big-doc codings above (those end well below base).
    if mega_code_codings > 0:
        mega_cid = n_codes  # last code
        big = file_texts[big_doc_fid]
        base, stride, seg = 60_000, 8, 6
        placed = 0
        k = 0
        while placed < mega_code_codings:
            pos0 = base + k * stride
            pos1 = pos0 + seg
            k += 1
            if pos1 > len(big):
                break
            add_coding(mega_cid, big_doc_fid, pos0, pos1)
            placed += 1

    cur.executemany(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,avid,important) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        coding_rows,
    )

    # ---- cases + whole-file case_text links ----
    case_rows = [(c, f"Case {c:03d}", f"memo case {c}", OWNER, DATE)
                 for c in range(1, n_cases + 1)]
    cur.executemany(
        "INSERT INTO cases (caseid,name,memo,owner,date) VALUES (?,?,?,?,?)", case_rows)

    case_text_rows = []
    ctextid = 1
    # Link hot files into several cases so the matrix join has heavy fan-out.
    for c in range(1, n_cases + 1):
        linked = [((c + j) % n_files) + 1 for j in range(3)]
        # ensure at least one hot file appears in a few cases
        if c <= n_hot_files:
            linked.append(hot_files[c - 1])
        for fid in set(linked):
            text = file_texts[fid]
            case_text_rows.append((ctextid, c, fid, 0, len(text), OWNER, DATE, ""))
            ctextid += 1
    cur.executemany(
        "INSERT INTO case_text (id,caseid,fid,pos0,pos1,owner,date,memo) VALUES (?,?,?,?,?,?,?,?)",
        case_text_rows,
    )

    # ---- attributes ----
    cur.execute("INSERT INTO attribute_type VALUES ('Age', ?, ?, '', 'case', 'numeric')",
                (DATE, OWNER))
    cur.execute("INSERT INTO attribute_type VALUES ('Site', ?, ?, '', 'case', 'character')",
                (DATE, OWNER))
    cur.execute("INSERT INTO attribute_type VALUES ('WordCount', ?, ?, '', 'file', 'numeric')",
                (DATE, OWNER))
    attr_rows = []
    aid = 1
    for c in range(1, n_cases + 1):
        attr_rows.append((aid, "Age", "case", str(20 + (c % 40)), c, DATE, OWNER)); aid += 1
        attr_rows.append((aid, "Site", "case", ["North", "South", "East"][c % 3], c, DATE, OWNER)); aid += 1
    for f in range(1, n_files + 1):
        attr_rows.append((aid, "WordCount", "file", str(len(file_texts[f].split())), f, DATE, OWNER)); aid += 1
    cur.executemany(
        "INSERT INTO attribute (attrid,name,attr_type,value,id,date,owner) VALUES (?,?,?,?,?,?,?)",
        attr_rows,
    )

    # ---- journals ----
    cur.executemany(
        "INSERT INTO journal (jid,name,jentry,date,owner) VALUES (?,?,?,?,?)",
        [(j, f"Journal {j}", _para(j, 10), DATE, OWNER) for j in range(1, 6)],
    )

    conn.commit()
    stats = {
        "files": n_files,
        "codes": n_codes,
        "categories": n_categories,
        "cases": n_cases,
        "codings": len(coding_rows),
        "case_text_links": len(case_text_rows),
        "big_doc_chars": len(big_text),
        "big_doc_fid": big_doc_fid,
        "hot_files": hot_files,
        "db_bytes": (project_folder / "data.qda").stat().st_size,
    }
    conn.close()
    return stats


def build_media_project(project_folder: Path) -> dict:
    """Build a mixed text + image/audio/video/PDF project with code_av /
    code_image rows, for graceful-degradation checks of the text tools."""
    conn = _connect(project_folder)
    cur = conn.cursor()

    # categories + codes
    cur.execute("INSERT INTO code_cat VALUES (1,'Visual',?,?, 'cat', NULL)", (OWNER, DATE))
    cur.execute("INSERT INTO code_cat VALUES (2,'Audio',?,?, 'cat', NULL)", (OWNER, DATE))
    codes = [
        (1, "Gesture", "", 1, OWNER, DATE, "#e6194B"),
        (2, "Tone", "", 2, OWNER, DATE, "#3cb44b"),
        (3, "Setting", "", 1, OWNER, DATE, "#4363d8"),
        (4, "Narrative", "", None, OWNER, DATE, "#f58231"),
    ]
    cur.executemany("INSERT INTO code_name VALUES (?,?,?,?,?,?,?)", codes)

    # sources: text, imported doc, pdf(with fulltext), image, audio, video, av-transcript
    txt = _para(1, 20)
    doc = _para(2, 25)
    pdf_text = _para(3, 30)
    transcript = _para(4, 15)  # transcript text of the audio/video (av_text_id target)
    sources = [
        # id, name, fulltext, mediapath, memo, owner, date, av_text_id, risid
        (1, "interview_text.txt", txt, None, "born-in-QC text", OWNER, DATE, None, None),
        (2, "report.docx.txt", doc, "/docs/report.docx", "imported doc", OWNER, DATE, None, None),
        (3, "handbook.pdf", pdf_text, "/docs/handbook.pdf", "imported pdf", OWNER, DATE, None, None),
        (4, "photo_scene.png", None, "/images/photo_scene.png", "scene photo", OWNER, DATE, None, None),
        (5, "diagram.jpg", None, "/images/diagram.jpg", "diagram", OWNER, DATE, None, None),
        (6, "call_recording.mp3", None, "/audio/call_recording.mp3", "audio only", OWNER, DATE, 8, None),
        (7, "session_video.mp4", None, "/video/session_video.mp4", "video only", OWNER, DATE, 8, None),
        # av transcript companion text source (QualCoder stores transcript as a text source)
        (8, "session_video.transcript.txt", transcript, None, "AV transcript", OWNER, DATE, None, None),
    ]
    cur.executemany(
        "INSERT INTO source (id,name,fulltext,mediapath,memo,owner,date,av_text_id,risid) "
        "VALUES (?,?,?,?,?,?,?,?,?)", sources)

    # text codings on the text-bearing sources (text, doc, pdf, transcript)
    def slc(fid_text, p0, p1):
        return fid_text[p0:p1]
    text_codings = [
        # ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,avid,important
        (1, 4, 1, slc(txt, 0, 15), 0, 15, OWNER, DATE, "", None, 0),
        (2, 3, 1, slc(txt, 20, 40), 20, 40, OWNER, DATE, "", None, 0),
        (3, 4, 2, slc(doc, 0, 18), 0, 18, OWNER, DATE, "", None, 0),
        (4, 4, 3, slc(pdf_text, 5, 25), 5, 25, OWNER, DATE, "pdf text coding", None, 0),
        (5, 2, 8, slc(transcript, 0, 12), 0, 12, OWNER, DATE, "on transcript", None, 0),
    ]
    cur.executemany(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,avid,important) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", text_codings)

    # code_image rows (on image sources + a pdf-page region)
    image_codings = [
        # imid,id,x1,y1,width,height,cid,memo,date,owner,important,pdf_page
        (1, 4, 10, 20, 100, 80, 1, "hand gesture region", DATE, OWNER, 1, None),
        (2, 4, 200, 50, 60, 60, 3, "background setting", DATE, OWNER, 0, None),
        (3, 5, 0, 0, 300, 300, 3, "whole diagram", DATE, OWNER, 0, None),
        (4, 3, 30, 40, 120, 90, 1, "figure on pdf page 2", DATE, OWNER, 0, 2),
    ]
    cur.executemany(
        "INSERT INTO code_image (imid,id,x1,y1,width,height,cid,memo,date,owner,important,pdf_page) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", image_codings)

    # code_av rows (on audio + video sources), pos in milliseconds
    av_codings = [
        # avid,id,pos0,pos1,cid,memo,date,owner,important
        (1, 6, 1000, 5000, 2, "warm tone", DATE, OWNER, 1),
        (2, 6, 8000, 12000, 4, "story begins", DATE, OWNER, 0),
        (3, 7, 2000, 9000, 2, "raised voice", DATE, OWNER, 0),
        (4, 7, 15000, 22000, 1, "points at whiteboard", DATE, OWNER, 0),
    ]
    cur.executemany(
        "INSERT INTO code_av (avid,id,pos0,pos1,cid,memo,date,owner,important) "
        "VALUES (?,?,?,?,?,?,?,?,?)", av_codings)

    # a case linking a couple of sources, an attribute, a journal
    cur.execute("INSERT INTO cases VALUES (1,'Participant A','', ?, ?)", (OWNER, DATE))
    cur.execute("INSERT INTO case_text VALUES (1,1,1,0,?,?,?,'')", (len(txt), OWNER, DATE))
    cur.execute("INSERT INTO attribute_type VALUES ('Modality', ?, ?, '', 'file', 'character')",
                (DATE, OWNER))
    cur.execute("INSERT INTO attribute VALUES (1,'Modality','file','video',7,?,?)", (DATE, OWNER))
    cur.execute("INSERT INTO journal VALUES (1,'J1','media coding notes',?,?)", (DATE, OWNER))

    conn.commit()
    stats = {
        "sources": len(sources),
        "text_codings": len(text_codings),
        "image_codings": len(image_codings),
        "av_codings": len(av_codings),
        "db_bytes": (project_folder / "data.qda").stat().st_size,
    }
    conn.close()
    return stats


def build_cooc_stress(project_folder: Path, n_codings: int, n_codes: int = 2) -> dict:
    """Pathological single-file project: n_codings heavily-overlapping code_text
    rows in ONE source, cycling n_codes codes. Isolates the O(n^2) code_text
    self-join in find_code_cooccurrences (all rows share one fid, all overlap)."""
    conn = _connect(project_folder)
    cur = conn.cursor()
    for c in range(1, n_codes + 1):
        cur.execute("INSERT INTO code_name VALUES (?,?,?,?,?,?,?)",
                    (c, f"Code {c:03d}", "", None, OWNER, DATE, COLORS[c % len(COLORS)]))
    seg_len, stride = 60, 4
    text = _para(1, 4)
    need = n_codings * stride + seg_len + 10
    while len(text) < need:
        text += " " + _para(len(text), 4)
    cur.execute("INSERT INTO source (id,name,fulltext,mediapath,owner,date) "
                "VALUES (1,'hot.txt',?,NULL,?,?)", (text, OWNER, DATE))
    rows = []
    for k in range(n_codings):
        p0 = k * stride
        p1 = p0 + seg_len
        rows.append((k + 1, (k % n_codes) + 1, 1, text[p0:p1], p0, p1, OWNER, DATE, "", None, 0))
    cur.executemany(
        "INSERT INTO code_text (ctid,cid,fid,seltext,pos0,pos1,owner,date,memo,avid,important) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return {"n_codings": n_codings, "n_codes": n_codes,
            "db_bytes": (project_folder / "data.qda").stat().st_size}


if __name__ == "__main__":
    import tempfile, json
    tmp = Path(tempfile.mkdtemp())
    s1 = build_scale_project(tmp / "smoke_scale.qda", n_files=30, n_codes=10,
                             n_categories=8, n_cases=6, target_codings=300,
                             big_doc_chars=20000, n_hot_files=2)
    s2 = build_media_project(tmp / "smoke_media.qda")
    print(json.dumps({"scale": s1, "media": s2}, indent=2))
