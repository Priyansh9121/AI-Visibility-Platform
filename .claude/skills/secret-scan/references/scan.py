#!/usr/bin/env python3
"""Value-safe secret sweep of a git repo. Run from the repo root.

    python3 scan.py tree    OUTFILE     committed files at HEAD (blobs, not the working tree)
    python3 scan.py history OUTFILE     every added line of `git log --all -p` (stash excluded)
    python3 scan.py all     OUTFILE     both, one fingerprint key for the whole run
    python3 scan.py triage  OUTFILE     facts about each finding in OUTFILE; prints no file text

Reads only git objects (`ls-tree`, `cat-file`, `log -p`, `branch -r`). It never opens a
working-tree file, so untracked .env / .mcp.json / settings.local.json are not read, and a
tracked symlink is never followed. Findings go to OUTFILE: mode, rule id, path:line, commit
short-sha (history), and a fingerprint. The matched text is NEVER written or printed. The
fingerprint is HMAC-SHA256 under a random per-run key that is discarded, truncated to 8 hex: it
only tells "same value" from "different value" inside that one OUTFILE. Do not quote it
anywhere else. Exit code is non-zero if git fails.

triage re-reads each finding's line in memory and prints ONE line of fixed fields per finding:
src rule loc commit remote cat name len ref placeholder pw_eq_user host in_example verdict.
Only two things are ever copied out of a blob: an identifier-shaped variable/key name from the
key side of the assignment (name_of) and, for URL findings, a host that is loopback or RFC 2606
reserved (*.test *.example *.invalid *.localhost example.com/net/org) (host_form). .internal and
single-label hosts are real private networks: private:<n labels>; any other host is
remote:<last two labels>. Everything else is a number, yes/no or a fixed word. verdict is
likely-placeholder only when the facts settle it (a test host plus a placeholder or user =
password; or a placeholder in a test/ci/docs/example path), else unclassified: unclassified
findings go to the founder, who looks at the line in their own terminal. Nobody else opens it.
"""
import functools, hashlib, hmac, os, re, secrets, subprocess, sys

# ---- scan rules (tree / history) -------------------------------------------------------
KW = r"(?:pass(?:word|wd)?|pwd|secret|token|api[_\-]?key|[_\-]key|credential|auth(?:orization|(?!or)))"
KEYWORD = KW + r"[\w\-]{0,40}['\"]?\s*[:=]\s*"
VALUE = (r"(?:\"[^\"]*\"?|'[^']*'?|(?!(?:true|false|none|null|nil|undefined)\b)"   # not a bare boolean/null
         r"[^\s'\"$<{(\[]\S{3,})")
LIT = r"\"[^\"\n]+\"|'[^'\n]+'"
LITERAL = "(?:" + LIT + ")"                       # a non-empty quoted literal
TOKEN_RULES = [  # value-shaped (the match is the secret)
    ("stripe-secret-key", r"\b[sr]k_(live|test)_[0-9A-Za-z]{16,}"),
    ("stripe-webhook-secret", r"\bwhsec_[0-9A-Za-z]{16,}"),
    ("anthropic-key", r"\bsk-ant-[0-9A-Za-z_\-]{20,}"),
    ("openai-key", r"\bsk-(proj-)?[0-9A-Za-z_\-]{32,}"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_\-]{35}"),
    ("aws-access-key-id", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", r"\b(gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{40,})"),
    ("slack-token", r"\bxox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("render-api-key", r"\brnd_[0-9A-Za-z]{20,}"),
    ("supabase-token", r"\bsbp_[0-9a-f]{40}"),
    ("resend-key", r"\bre_[0-9A-Za-z]{20,}"),
    ("slack-webhook-url", r"https://hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9/_\-]{8,}"),
    ("discord-webhook-url", r"https://(?:discord|discordapp)\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{8,}"),
    ("private-key-block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
    ("url-with-password", r"\b[a-z][a-z0-9+.\-]*://[^\s:/@'\"]*:[^\s@'\"]{1,}@[^\s'\"]+"),
]
ASSIGN_RULES = [  # keyword-shaped (the match is NAME, separator, value)
    ("keyword-assignment", r"(?i)\b[\w\-]{0,40}" + KEYWORD + VALUE),
    # typed default:  api_key: str = "x"   app_secret: SecretStr = SecretStr("x")   Field(default="x")
    ("typed-default-assignment",
     r"(?i)\b[\w\-]{0,40}" + KW + r"[\w\-]{0,40}\s*:\s*[^=\n'\"]{1,80}=\s*(?:[\w.]{1,40}\(\s*)?"
     r"(?:default\s*=\s*)?[rbuf]{0,2}" + LITERAL),
    # setenv("X_SECRET", "x")   os.environ.setdefault("X_KEY", "x")   os.putenv(...)
    ("setenv-literal",
     r"(?i)\b(?:setenv|putenv|setdefault|set_env)\s*\(\s*['\"][^'\"\n]{0,60}" + KW +
     r"[^'\"\n]{0,60}['\"]\s*,\s*" + LITERAL),
    # os.environ["X_PASSWORD"] = "x"
    ("environ-assignment",
     r"(?i)\benviron\s*\[\s*['\"][^'\"\n]{0,60}" + KW + r"[^'\"\n]{0,60}['\"]\s*\]\s*=\s*" + LITERAL),
    # process.env.X_SECRET = "x"   process.env["X_KEY"] = 'x'
    ("process-env-assignment",
     r"(?i)\bprocess\.env(?:\.\w{0,60}" + KW + r"\w{0,60}|\[\s*['\"`][^'\"`\n]{0,60}" + KW +
     r"[^'\"`\n]{0,60}['\"`]\s*\])\s*=\s*(?:" + LIT + r"|`[^`\n]+`)"),
    ("generic-assignment",
     r"(?i)\b[\w\-]{0,40}(password|passwd|secret|token|api[_\-]?key)[\w\-]{0,40}['\"]?\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"),
]
COMPILED = [(n, re.compile(p)) for n, p in TOKEN_RULES + ASSIGN_RULES]
TOKEN_COMPILED = [(n, re.compile(p)) for n, p in TOKEN_RULES]
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2",
            ".ttf", ".zip", ".gz", ".lock", ".map")
MAX_LINE = 4000          # longer lines are cut before matching
KEY = secrets.token_bytes(32)
GIT = ["git", "-c", "core.quotePath=false"]


def fp(text):
    return hmac.new(KEY, text.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:8]


def hits(line):  # first match per rule per line
    line = line[:MAX_LINE]
    for name, rx in COMPILED:
        m = rx.search(line)
        if m:
            yield name, fp(m.group(0))


def scan_tree(out):
    n = 0
    ls = subprocess.run(GIT + ["ls-tree", "--full-tree", "-r", "-z", "HEAD"], capture_output=True)
    if ls.returncode:
        raise RuntimeError(f"git ls-tree failed rc={ls.returncode}")
    cat = subprocess.Popen(GIT + ["cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for rec in ls.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, rawpath = rec.partition(b"\t")
        mode, typ, sha = meta.decode().split()
        path = rawpath.decode("utf-8", "replace")
        if typ != "blob" or mode not in ("100644", "100755") or path.lower().endswith(SKIP_EXT):
            continue                     # symlinks (120000) and submodules are not read
        cat.stdin.write(sha.encode() + b"\n"); cat.stdin.flush()
        header = cat.stdout.readline().split()
        size = int(header[2])
        data = cat.stdout.read(size); cat.stdout.read(1)
        if b"\0" in data[:8000]:
            continue
        for i, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            for name, f in hits(line):
                out.write(f"tree\t{name}\t{path}:{i}\t{f}\n"); n += 1
    cat.stdin.close()
    if cat.wait():
        raise RuntimeError("git cat-file failed")
    return n


def header_path(h):
    """Path from the text after '+++ ': handles git's C-style quoting; None for /dev/null."""
    h = h.split("\t")[0] if not h.startswith('"') else h
    if h.startswith('"'):
        body, raw, i = h.rstrip()[1:-1], bytearray(), 0
        esc = {"t": 9, "n": 10, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, '"': 34, "\\": 92}
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                d = body[i + 1]
                if d in "01234567":
                    j = i + 1
                    while j < len(body) and j < i + 4 and body[j] in "01234567":
                        j += 1
                    raw.append(int(body[i + 1:j], 8) & 255); i = j; continue
                raw.append(esc.get(d, ord(d))); i += 2; continue
            raw += c.encode("utf-8"); i += 1
        h = raw.decode("utf-8", "replace")
    return h[2:] if h.startswith("b/") else None


def read_lines(stream, cap=1 << 20):
    """Yield text lines; a line longer than cap bytes is cut (the rest is skipped), not buffered."""
    while True:
        chunk = stream.readline(cap)
        if not chunk:
            return
        if not chunk.endswith(b"\n"):          # over-long line: drop the remainder
            while True:
                rest = stream.readline(cap)
                if not rest or rest.endswith(b"\n"):
                    break
        yield chunk.decode("utf-8", "replace").rstrip("\n")


def scan_history(out):
    # Flags pin the output shape whatever the user's git config says (log.showRoot,
    # diff.interHunkContext, diff.relative, diff.renames, log.showSignature, -diff attributes).
    p = subprocess.Popen(
        GIT + ["log", "--exclude=refs/stash", "--all", "--root", "-p", "-U0", "--inter-hunk-context=0",
               "--no-relative", "--text", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
               "--no-show-signature", "--default-prefix", "--format=@@COMMIT %h"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    commit = path = None
    lineno = rem_old = rem_new = 0
    minus_hdr = False
    seen = {}   # (rule, fp, path) -> [oldest commit, path:line, count]
    for s in read_lines(p.stdout):
        in_hunk = rem_old > 0 or rem_new > 0
        if not in_hunk and s.startswith("@@COMMIT "):
            commit = s.split()[1]; path = None; minus_hdr = False
        elif not in_hunk and s.startswith("--- "):
            minus_hdr = True
        elif not in_hunk and minus_hdr and s.startswith("+++ "):
            minus_hdr = False
            path = header_path(s[4:])
            if path and path.lower().endswith(SKIP_EXT):
                path = None
        elif not in_hunk and s.startswith("@@ "):
            m = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", s)
            if m:
                rem_old = int(m.group(1)) if m.group(1) is not None else 1
                lineno = int(m.group(2))
                rem_new = int(m.group(3)) if m.group(3) is not None else 1
            minus_hdr = False
        elif in_hunk:
            if s.startswith("+"):
                rem_new -= 1
                if path:
                    for name, f in hits(s[1:]):
                        key = (name, f, path)
                        if key in seen:   # log is newest-first: the last one seen is the oldest
                            seen[key] = [commit, f"{path}:{lineno}", seen[key][2] + 1]
                        else:
                            seen[key] = [commit, f"{path}:{lineno}", 1]
                lineno += 1
            elif s.startswith("-"):
                rem_old -= 1
            elif s.startswith(" ") or s == "":   # unchanged line inside a hunk (git config can add them)
                rem_old -= 1; rem_new -= 1; lineno += 1
        else:
            minus_hdr = False
    if p.wait():
        raise RuntimeError(f"git log failed rc={p.returncode}")
    for (name, f, _), (c, loc, cnt) in seen.items():
        out.write(f"history\t{name}\t{loc}\tcommit {c}\t{f}\tadded {cnt}x\n")
    return len(seen)


# ---- triage: facts about each finding; prints NO text from the file ---------------------------
RX, ASSIGN, HEX = dict(COMPILED), {n for n, _ in ASSIGN_RULES}, re.compile(r"HEAD|[0-9a-f]{4,40}")
URLS = ("url-with-password", "slack-webhook-url", "discord-webhook-url")
URL = re.compile(r"://(?:([^:/@]*):([^@]*)@)?(?:\[([0-9a-fA-F:]+)\]|([^/?#:]*))")   # user, password, host
WORDS = set("changeme change me password passw0rd passwd postgres secret example test testing dummy demo admin root default "
            "redacted placeholder replace replaceme fake insecure dev only your here sample none null todo tbd".split())
TEMPLATE = re.compile(r"x{3,}|\*{3,}|\.{3,}|-+|<[a-z0-9 _\-]+>|\{\{[a-z0-9 _.\-]+\}\}|your[-_ .][a-z0-9 _.\-]+[-_ .]here", re.I | re.A)    # the whole value
FIRST = re.compile(r"(?:[ \t\-{\[(,]|export(?![A-Za-z0-9_.\-]))*")    # all that may precede a name on its line (its own opening quote aside)
NAME_PRE = {"setenv-literal": r"\w+\s*\(\s*[\"'`]", "environ-assignment": r"environ\s*\[\s*[\"'`]",
            "process-env-assignment": r"process\.env(?:\.|\s*\[\s*[\"'`])"}   # where the key sits in these three matches
REFERENCE = re.compile(r"\$\{|\$[A-Za-z_]|os\.environ|process\.env|secrets\.|\{\{")
IDENT = r"([A-Za-z_][A-Za-z0-9_.\-]{0,63})"
RANDOMISH = re.compile(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}")
CATEGORY = [("example", r"\.(?:example|sample)$"), ("docs", r"^docs?/|\.(?:md|mdx|rst)$"),
            ("test", r"(?:^|/)(?:tests?|__tests__|fixtures|e2e|spec)/|(?:^|/)(?:test_|conftest)|[._]test\.|\.spec\."),
            ("ci", r"^(?:\.github|\.circleci|infra/ci)/|(?:^|/)(?:\.gitlab-ci\.yml|jenkinsfile)$"),
            ("src", r"\.(?:py|ts|tsx|js|jsx|mjs|go|rs|java|rb|php|sh|sql)$")]


@functools.lru_cache(maxsize=16)
def git(*args):
    r = subprocess.run(GIT + list(args), capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def category(path):
    return next((c for c, rx in CATEGORY if re.search(rx, path.lower())), "other")


def on_remote(sha):
    out = git("branch", "-r", "--contains", sha) if HEX.fullmatch(sha) else None
    return "unknown" if out is None else "yes" if out.strip() else "no" if (git("branch", "-r") or "").strip() else "unknown"


def parse(rule, text):
    """(match, secret part, start of the match) for the rule's first match in a line, else None."""
    m = RX[rule].search(text[:MAX_LINE])
    if not m:
        return None
    t = m.group(0)
    if rule == "url-with-password":
        return t, URL.search(t).group(2), m.start()
    if rule not in ASSIGN:
        return t, t, m.start()
    q = re.search(r"([\"'`])((?:(?!\1).)+)\1$", t)          # a quoted literal, else everything after the separator
    return t, q.group(2) if q else re.split(r"[:=]\s*", t, maxsplit=1)[-1].strip("\"' "), m.start()


def is_placeholder(v):  # the WHOLE value: a template, or ASCII words joined by - _ space . that are all placeholder words; never with a random-looking run
    words = re.split(r"[-_ .]+", v.lower()) if re.fullmatch(r"[a-z0-9]+(?:[-_ .]+[a-z0-9]+)*", v, re.I | re.A) else []
    return not RANDOMISH.search(v) and bool(TEMPLATE.fullmatch(v) or (words and all(w in WORDS for w in words)) or re.search(r"(.)\1{7,}", v))


def safe(x):  # the only strings copied out of a blob must not look like a secret
    return None if x is None or RANDOMISH.search(x) or any(rx.search(x) for _, rx in TOKEN_COMPILED) else x


def open_quote(s):  # the quote character still open at the end of s, else None
    q = None
    for c in s:
        q = (None if c == q else q) if q else (c if c in "\"'`" else None)
    return q


def name_of(rule, t, ms, text):  # copies bytes out of the blob: the first token on its line, from the key side, never from the value
    if rule in ASSIGN:      # NAME= / NAME: at the start of the match; setenv("NAME", ..) / environ["NAME"] / process.env.NAME
        m = re.match(NAME_PRE.get(rule, "") + IDENT + r"(?![A-Za-z0-9_.\-])", t)
        pre, own = re.sub(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+$", "", text[:ms]), rule not in NAME_PRE    # a receiver (self. / os.environ.) is not a token
    else:                   # value-shaped rule: the identifier before the first = or : (not ://) outside any quoted string
        left = text[:ms]
        i = next((i for i, c in enumerate(left) if c in "=:" and not left.startswith("://", i) and not open_quote(left[:i])), None)
        m = None if i is None else re.search(r"(?<![A-Za-z0-9_.\-])" + IDENT + r"[\"'\]]*\s*$", left[:i])
        pre, own = (left[:m.start(1)] if m else ""), True
    if own and pre[-1:] in ("\"", "'", "`"):
        pre = pre[:-1]      # the name's own opening quote
    return safe(m.group(1) if m and FIRST.fullmatch(pre) else None)    # anything else before it on the line: none


def host_form(h):  # copies bytes out of the blob: only a loopback / RFC 2606 reserved host; private names are counted, not named
    l = h.rstrip(".").split(".")
    if not re.fullmatch(r"[a-z0-9.:\-]{1,253}", h) or max(map(len, l)) > 30:
        return "remote:?"
    if re.fullmatch(r"127(\.\d{1,3}){3}|::1|localhost", h) or l[-1] in ("test", "example", "invalid", "localhost") or ".".join(l[-2:]) in ("example.com", "example.net", "example.org"):
        return safe(h) or "remote:?"
    if re.fullmatch(r"[0-9.]+|[0-9a-f]*:[0-9a-f:]*", h):
        return "remote:ip"
    if len(l) == 1 or l[-1] == "internal":
        return f"private:{len(l)}"
    k = 3 if len(l) > 2 and len(l[-1]) == 2 and l[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") else 2    # co.uk, com.au
    return safe("remote:" + ".".join(l[-k:])) or "remote:?"


def triage(report):
    recs = [f for f in (x.split("\t") for x in open(report).read().splitlines()) if f[0] in ("tree", "history")
            and len(f) == (4 if f[0] == "tree" else 6) and f[1] in RX and f[2].rpartition(":")[2].isdigit()]
    head, fps = (git("rev-parse", "--short", "HEAD") or "").strip(), lambda f: f[-1] if f[0] == "tree" else f[-2]
    example_fps = {fps(f) for f in recs if category(f[2].rpartition(":")[0]) == "example"}
    for f in recs:
        src, rule, (path, _, num) = f[0], f[1], f[2].rpartition(":")
        sha = head if src == "tree" else f[3].split()[-1]
        blob = (git("cat-file", "blob", f"{'HEAD' if src == 'tree' else sha}:{path}") if HEX.fullmatch(sha) else None) or ""
        lines = blob.split("\n")
        text = lines[int(num) - 1] if 0 < int(num) <= len(lines) else ""
        h = parse(rule, text)
        t, v, ms = h or ("", "", 0)
        cat, yn = category(path), (lambda b: ("yes" if b else "no") if h else "n/a")
        u = URL.search(t) if h and rule in URLS else None
        host = host_form((u.group(3) or u.group(4) or "").lower()) if u else "n/a"
        pw_eq = yn(u.group(1) == u.group(2)) if u and rule == "url-with-password" else "n/a"
        ph, inex = yn(is_placeholder(v)), yn(fps(f) in example_fps)
        test_host = bool(u) and not host.startswith(("remote:", "private:"))    # printed as-is = loopback or reserved
        settled = test_host if u else cat in ("test", "ci", "docs", "example")
        evidence = ph == "yes" or (rule == "url-with-password" and pw_eq == "yes")    # an example file alone is never enough
        verdict = "likely-placeholder" if h and settled and evidence else "unclassified"
        print("\t".join(f"{k}={x}" for k, x in (
            ("src", src), ("rule", rule), ("loc", path.encode("unicode_escape").decode() + ":" + num),
            ("commit", sha if HEX.fullmatch(sha) else "unknown"), ("remote", on_remote(sha)), ("cat", cat),
            ("name", (name_of(rule, t, ms, text) if h else None) or "none"), ("len", len(v) if h else "n/a"),
            ("ref", yn(REFERENCE.search(v))), ("placeholder", ph), ("pw_eq_user", pw_eq), ("host", host),
            ("in_example", inex), ("verdict", verdict))))


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if len(a) == 2 and a[0] == "triage":
            try:
                triage(a[1])
            except Exception:       # an exception message could quote file text: print a fixed one
                sys.exit("scan.py: triage failed")
        elif len(a) == 2 and a[0] in ("tree", "history", "all"):
            with open(a[1], "a") as out:
                n = 0
                if a[0] in ("tree", "all"):
                    n += scan_tree(out)
                if a[0] in ("history", "all"):
                    n += scan_history(out)
            print(f"{a[0]}: {n} finding line(s) written (values never printed)")
        else:
            sys.exit("usage: scan.py tree|history|all|triage OUTFILE")
    except RuntimeError as e:
        sys.exit(f"scan.py: {e}")
