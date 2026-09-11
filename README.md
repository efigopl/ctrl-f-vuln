# ctrl-f-vuln

## Purpose
ctrl-f-vuln is a tool for searching GitHub for code samples to identify vulnerabilities in
open source projects. It provides a command-line interface for data collection and a
Flask-based web viewer for triaging the results: browsing, filtering, previewing file
contents with the matched pattern highlighted, and marking files as reviewed.

## Features
- Search GitHub code with custom queries, storing results in a local SQLite database
- Sweep broad queries in `size:` windows to get past the API's 1000-result-per-query limit
- Resume an interrupted search from where it stopped
- Augment repository records with stargazer counts
- Web viewer with filtering, sorting, paging, bulk actions and keyboard-driven triage
- Preview a file's contents in-app with the search terms highlighted and match navigation
- Clone a repository (shallow by default) and open it in your editor

## Requirements
- Python 3.10+
- A GitHub API token (for code search, augmentation, and unthrottled previews)

## Installation
1. Clone the repository:
   ```sh
   git clone https://github.com/efigopl/ctrl-f-vuln.git
   cd ctrl-f-vuln
   ```
2. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
3. Generate a config file and add your token:
   ```sh
   python search_github.py config
   # then edit .env and set GITHUB_TOKEN
   ```

## Usage

### CLI
```sh
python search_github.py <project> <command> [options]
```

| Command | What it does |
| --- | --- |
| `search` | Search GitHub code and store the results. Requires `--query`. |
| `resume` | Continue the project's most recent unfinished search. |
| `augment` | Fetch missing stargazer counts for this project's repositories. |
| `list` | List the project's repositories. |
| `stats` | Show counts and review progress for the project. |
| `config` | Write a template `.env` file. |
| `migrate` | Apply indexes and schema updates to an existing database. |

Options: `--query`, `--token`, `--db`, `--limit`, `--all-projects`, `-v/--verbose`,
`-q/--quiet`.

Examples:
```sh
python search_github.py php-search search --query "mysql_query("
python search_github.py php-search resume
python search_github.py php-search augment
python search_github.py php-search stats
```

`config` and `migrate` ignore the project argument, so they can be run as
`python search_github.py config`.

### Web viewer
```sh
python web_viewer.py
```
Then open http://127.0.0.1:5000/.

Choose a different interface or port with flags, which override `CTRLF_HOST` and
`CTRLF_PORT`:
```sh
python web_viewer.py --port 8080                  # localhost, different port
python web_viewer.py --host 0.0.0.0 --port 8080   # every IPv4 interface
python web_viewer.py --host 192.168.1.10          # one specific interface
python web_viewer.py --host ::1                   # IPv6 loopback
python web_viewer.py --port 0                     # let the OS pick a free port
```

| Flag | Purpose |
| --- | --- |
| `--host`, `--interface` | Interface to bind [`127.0.0.1`] |
| `-p`, `--port` | Port to listen on; `0` picks a free one [`5000`] |
| `--db` | SQLite database path |
| `--debug` / `--no-debug` | Flask reloader and debugger |
| `-v`, `--verbose` | Debug logging |

The viewer binds to localhost by default, and for good reason: it has no
authentication and can clone repositories and launch an editor on the machine it runs
on. Binding to a reachable interface logs a warning; prefer an SSH tunnel
(`ssh -L 5000:127.0.0.1:5000 host`) or a firewall rule over exposing it directly.
Debug mode on a non-loopback interface is refused outright, because the Werkzeug
debugger would let anyone who can reach the port run code on that machine.

For a real deployment, point a WSGI server at the app instead of using the development
server:
```sh
gunicorn -b 127.0.0.1:8080 web_viewer:app
```

**Triage controls**
- Filter by project, minimum stars, and a substring of the file name, path or repository
- Sort by any column; toggle "Show reviewed" to include files already done
- Click a file name or **Preview** to read it in-app, with the query's literal terms
  highlighted and next/previous match navigation
- Mark files reviewed (or un-review them) without a page reload; select rows for bulk
  actions
- Every filter is carried across paging and sorting links

**Keyboard shortcuts** (press <kbd>?</kbd> in the app for the full list)

| Key | Action |
| --- | --- |
| `j` / `k` | Next / previous row |
| `x` | Toggle reviewed |
| `p` / `Enter` | Preview the focused file |
| `o` | Open the focused file on GitHub |
| `s` | Select the row for a bulk action |
| `n` / `N` | Next / previous match in the preview |
| `/` | Focus the filter box |
| `Esc` | Close the preview |

## Configuration
All settings are read from the environment or `.env`. Defaults in brackets.

| Variable | Purpose |
| --- | --- |
| `GITHUB_TOKEN` | API token for search, augmentation and previews |
| `CTRLF_DB_PATH` | SQLite database path [`ctrl_f_vuln.db`] |
| `CTRLF_PROJECTS_DIR` | Where repositories are cloned [`projects`] |
| `CTRLF_EDITOR` | Editor command; auto-detected from PATH when unset |
| `CTRLF_HOST` / `CTRLF_PORT` | Viewer bind address [`127.0.0.1`, `5000`]; `--host`/`--port` win |
| `CTRLF_DEBUG` | Flask debug mode [`False`] |
| `CTRLF_LOG_LEVEL` | Logging level [`INFO`] |
| `CTRLF_PAGE_SIZE` | Rows per page [`100`] |
| `CTRLF_COUNT_CAP` | Stop counting matches past this number [`10000`] |
| `CTRLF_PREVIEW_MAX_BYTES` | Largest file body fetched for preview [`512000`] |
| `CTRLF_AUGMENT_DELAY` | Seconds between augment requests [`0.75`] |
| `CTRLF_CLONE_DEPTH` | `git clone --depth`; `0` for full history [`1`] |
| `CTRLF_SECRET_KEY` | Flask session key; random per process when unset |

## Upgrading an existing database
Run the migration once. It is idempotent and safe on a populated database:
```sh
python search_github.py migrate
```
It adds the indexes the viewer's queries rely on, switches the database to WAL mode (so
the viewer can be read while a search is writing), and adds the columns used to resume a
search.

Clone directories are now namespaced by owner (`projects/<project>/<owner>/<repo>`)
because the previous flat layout made `alice/utils` and `bob/utils` collide. Clones made
by an earlier version are still detected in their old location.

## Database schema
`Projects`, `Searches`, `Repositories`, `Files`, `FileSearches`.

## Development
```sh
pip install -r requirements-dev.txt
python -m pytest
```

## Notes
- The GitHub API rate-limits aggressively. The client honours `Retry-After` and
  `X-RateLimit-Reset`, and retries transient failures with backoff.
- Code search returns at most 1000 results per query; `search` works around this by
  splitting the query into `size:` windows and narrowing any window that overflows.
- A repository that has been deleted is recorded as unavailable rather than retried
  forever.
- `.env`, the database, and cloned repositories are excluded from version control.
