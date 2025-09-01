import os
import sqlite3
import argparse
from decouple import Config, RepositoryEnv
ENV_FILE = '.env'
config_env = Config(repository=RepositoryEnv(ENV_FILE) if os.path.exists(ENV_FILE) else None)
import requests
import json
from datetime import datetime
from time import sleep

def parse_args():
    parser = argparse.ArgumentParser(description="Search GitHub for code samples to identify vulnerabilities in open source projects.")
    parser.add_argument('project', type=str, help='Project name for which the command is performed')
    parser.add_argument('command', type=str, choices=['search', 'list', 'resume', 'config', 'augment', 'stats'], help='Command to execute: search, list, resume, config, augment, stats')
    parser.add_argument('--query', type=str, help='Search query for GitHub code search. Required for search command.')
    parser.add_argument('--token', type=str, help='GitHub API token for authentication. Required for search and resume commands.')
    return parser.parse_args()


def main():
    initialize_database()
    args = parse_args()
    initialize_folders(args.project)

    # Use token from .env if not provided as argument
    token = args.token or config_env.get('GITHUB_TOKEN', default=None)
    if args.command == 'search':
        if not args.query or not token:
            print('The search command requires --query and --token (either as argument or in .env config).')
            return
        run_search(args, token)
    elif args.command == 'list':
        run_list(args)
    elif args.command == 'resume':
        if not token:
            print('The resume command requires --token (either as argument or in .env config).')
            return
        run_resume(args, token)
    elif args.command == 'augment':
        if not token:
            print('The augment command requires --token (either as argument or in .env config).')
            return
        run_augment(token)
    elif args.command == 'stats':
        run_stats(args)
    elif args.command == 'config':
        generate_config_template()
    else:
        print(f"Unknown command: {args.command}")
# Show database stats for a given project
def run_stats(args):
    print(f"Database stats for project: {args.project}")
    conn = sqlite3.connect('ctrl_f_vuln.db')
    cursor = conn.cursor()
    # Get project id
    cursor.execute('SELECT id FROM Projects WHERE name=?', (args.project,))
    row = cursor.fetchone()
    if not row:
        print("Project not found.")
        conn.close()
        return
    project_id = row[0]
    # Number of searches
    cursor.execute('SELECT COUNT(*) FROM Searches WHERE project_id=?', (project_id,))
    searches = cursor.fetchone()[0]
    # Number of repositories
    cursor.execute('''
        SELECT COUNT(DISTINCT r.id)
        FROM Repositories r
        JOIN Files f ON r.id = f.repository_id
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON fs.search_id = s.id
        WHERE s.project_id=?
    ''', (project_id,))
    repos = cursor.fetchone()[0]
    # Number of files
    cursor.execute('''
        SELECT COUNT(DISTINCT f.id)
        FROM Files f
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON fs.search_id = s.id
        WHERE s.project_id=?
    ''', (project_id,))
    files = cursor.fetchone()[0]
    # Number of checked files
    cursor.execute('''
        SELECT COUNT(DISTINCT f.id)
        FROM Files f
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON fs.search_id = s.id
        WHERE s.project_id=? AND f.checked=1
    ''', (project_id,))
    checked_files = cursor.fetchone()[0]
    print(f"Searches: {searches}")
    print(f"Repositories: {repos}")
    print(f"Files: {files}")
    print(f"Checked files: {checked_files}")

    # Top 10 repositories by stargazers_count
    print("\nTop 10 repositories by stars:")
    cursor.execute('''
        SELECT r.full_name, r.stargazers_count, r.html_url
        FROM Repositories r
        JOIN Files f ON r.id = f.repository_id
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON fs.search_id = s.id
        WHERE s.project_id=? AND r.stargazers_count >= 0
        GROUP BY r.id
        ORDER BY r.stargazers_count DESC
        LIMIT 10
    ''', (project_id,))
    top_repos = cursor.fetchall()
    for i, (name, stars, url) in enumerate(top_repos, 1):
        print(f"{i}. {name} - {stars} stars - {url}")
    conn.close()
# Augment command: update stargazers_count for repos with -1
def run_augment(token):
    print("Augmenting repositories with missing stargazer counts...")
    conn = sqlite3.connect('ctrl_f_vuln.db')
    cursor = conn.cursor()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github+json"
    }
    while True:
        cursor.execute('SELECT id, full_name FROM Repositories WHERE stargazers_count = -1 LIMIT 1000')
        repos = cursor.fetchall()
        if not repos:
            break
        for repo_id, full_name in repos:
            url = f"https://api.github.com/repos/{full_name}"
            try:
                r = requests.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    stargazers = data.get('stargazers_count', -1)
                    cursor.execute('UPDATE Repositories SET stargazers_count=? WHERE id=?', (stargazers, repo_id))
                    conn.commit()
                    print(f"Updated {full_name}: {stargazers} stargazers")
                else:
                    print(f"Failed to fetch {full_name}: {r.status_code}")
            except Exception as e:
                print(f"Error fetching {full_name}: {e}")
            # sleep(1)  # avoid rate limit
    conn.close()
    print("Augmentation complete.")

# Generate a template .env config file
def generate_config_template():
    env_path = '.env'
    if os.path.exists(env_path):
        print(f"Config file '{env_path}' already exists.")
        return
    with open(env_path, 'w') as f:
        f.write("# GitHub API Token\n")
        f.write("GITHUB_TOKEN=your_github_token_here\n")
    print(f"Template config file '{env_path}' created.")

def run_search(args, token):
    conn = sqlite3.connect('ctrl_f_vuln.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO Projects (name) VALUES (?)', (args.project,))
    conn.commit()
    cursor.execute('SELECT id FROM Projects WHERE name=?', (args.project,))
    project_id = cursor.fetchone()[0]
    now = datetime.utcnow().isoformat()
    cursor.execute('INSERT INTO Searches (project_id, query, date, finished) VALUES (?, ?, ?, 0)', (project_id, args.query, now))
    search_id = cursor.lastrowid
    conn.commit()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github+json"
    }
    per_page = 100
    sleep_time = 60
    size_start = 0
    total_count = 0
    size_step = 100
    while True:
        size_end = size_start + size_step
        full_query = args.query + f" size:{size_start}..{size_end}"
        page = 1
        total_count = None
        fetched = 0
        while True:
            params = {'q': full_query, 'per_page': per_page, 'page': page}
            try:
                r = requests.get("https://api.github.com/search/code", params=params, headers=headers)
                if r.status_code != 200:
                    if r.status_code == 422:
                        size_step = max(1, size_step // 2)
                        print(f"Too many results ({total_count}) in window, reducing size_step to {size_step} and retrying...")
                        items = None
                        break
                    else:
                        print(f"GitHub API error: {r.status_code}")
                        print(r.text)
                        sleep(sleep_time)
                    continue
                r_data = r.json()
                items = None
                if total_count is None:
                    total_count = r_data.get('total_count', 0)
                    print(f"Total results for size range {size_start}..{size_end}: {total_count}")
                    if total_count > 900:
                        # Reduce window and retry
                        size_step = max(1, size_step // 2)
                        print(f"Too many results ({total_count}) in window, reducing size_step to {size_step} and retrying...")
                        break
                    if total_count < 100:
                        # Increase window and retry
                        size_step = round(size_step * 1.5)
                        print(f"Not enough results ({total_count}) in window, increasing size_step to {size_step} and retrying...")
                        break
                items = r_data.get('items', [])
                if not items:
                    break
                for item in items:
                    repo = item['repository']
                    cursor.execute('SELECT id FROM Repositories WHERE id=?', (repo['id'],))
                    repo_row = cursor.fetchone()
                    if not repo_row:
                        cursor.execute('''INSERT INTO Repositories (id, node_id, name, full_name, private, owner_login, owner_id, html_url, description, fork, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (repo['id'], repo['node_id'], repo['name'], repo['full_name'], int(repo['private']), repo['owner']['login'], repo['owner']['id'], repo['html_url'], repo.get('description'), int(repo['fork']), repo['url']))
                    # Check if file already exists
                    cursor.execute('''SELECT id FROM Files WHERE repository_id=? AND name=? AND path=? AND sha=?''',
                        (repo['id'], item['name'], item['path'], item['sha']))
                    file_row = cursor.fetchone()
                    if file_row:
                        file_id = file_row[0]
                    else:
                        cursor.execute('''INSERT INTO Files (repository_id, name, path, sha, url, git_url, html_url, score, checked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)''',
                            (repo['id'], item['name'], item['path'], item['sha'], item['url'], item['git_url'], item['html_url'], item.get('score', 0)))
                        file_id = cursor.lastrowid
                    cursor.execute('INSERT OR IGNORE INTO FileSearches (file_id, search_id) VALUES (?, ?)', (file_id, search_id))
                conn.commit()
                fetched += len(items)
                total_count += len(items)
                print(f"Fetched {fetched} (page {page}) in size range {size_start}..{size_end}")
                if len(items) < per_page:
                    break
                page += 1
                sleep(1)
            except Exception as e:
                print(f"Error: {e}")
                sleep(sleep_time)
                
        if total_count == 0:
            # TODO: can beak on local zero size range
            sleep(sleep_time)
            r = requests.get("https://api.github.com/search/code", params={'q': args.query + f" size:>{size_start}", 'per_page': per_page, 'page': page}, headers=headers)
            if r.json().get('total_count', 0) == 0:
                break
        if items != None:
            size_start += size_step
            sleep(sleep_time)
            print(f"Total count: {fetched}")

    cursor.execute('UPDATE Searches SET finished=1 WHERE id=?', (search_id,))
    conn.commit()
    conn.close()

# Placeholder functions for other commands
def run_list(args):
    print(f"Repositories fetched for project: {args.project}")
    conn = sqlite3.connect('ctrl_f_vuln.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT r.id, r.full_name, r.html_url, r.stargazers_count
        FROM Repositories r
        JOIN Files f ON r.id = f.repository_id
        JOIN Projects p ON p.name = ?
        JOIN Searches s ON s.project_id = p.id
        JOIN FileSearches fs ON fs.search_id = s.id AND fs.file_id = f.id
    ''', (args.project,))
    repos = cursor.fetchall()
    if not repos:
        print("No repositories found for this project.")
    else:
        for repo in repos:
            print(f"ID: {repo[0]}, Name: {repo[1]}, URL: {repo[2]}, Stargazers: {repo[3]}")
    conn.close()

def run_resume(args):
    print(f"Resuming search for project: {args.project}")
    # TODO: Implement resume logic

def run_config(args):
    print(f"Config for project: {args.project}")
    # TODO: Implement config logic

# Folder initialization function
def initialize_folders(project_name):
    base_folder = 'projects'
    project_folder = os.path.join(base_folder, project_name)
    if not os.path.exists(base_folder):
        print(f"Creating folder: {base_folder}")
        os.makedirs(base_folder)
    if not os.path.exists(project_folder):
        print(f"Creating folder: {project_folder}")
        os.makedirs(project_folder)


def initialize_database(db_name='ctrl_f_vuln.db'):
    if not os.path.exists(db_name):
        print(f"Database '{db_name}' not found. Creating new SQLite database.")
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE Projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        ''')
        cursor.execute('''
            CREATE TABLE Searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                date TEXT NOT NULL,
                finished BOOLEAN NOT NULL DEFAULT 0,
                FOREIGN KEY (project_id) REFERENCES Projects(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE Repositories (
                id INTEGER PRIMARY KEY,
                node_id TEXT,
                name TEXT,
                full_name TEXT,
                private BOOLEAN,
                owner_login TEXT,
                owner_id INTEGER,
                html_url TEXT,
                description TEXT,
                fork BOOLEAN,
                url TEXT,
                stargazers_count INTEGER DEFAULT -1
            )
        ''')
        cursor.execute('''
            CREATE TABLE Files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repository_id INTEGER NOT NULL,
                name TEXT,
                path TEXT,
                sha TEXT,
                url TEXT,
                git_url TEXT,
                html_url TEXT,
                score REAL,
                checked BOOLEAN NOT NULL DEFAULT 0,
                FOREIGN KEY (repository_id) REFERENCES Repositories(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE FileSearches (
                file_id INTEGER NOT NULL,
                search_id INTEGER NOT NULL,
                PRIMARY KEY (file_id, search_id),
                FOREIGN KEY (file_id) REFERENCES Files(id) ON DELETE CASCADE,
                FOREIGN KEY (search_id) REFERENCES Searches(id) ON DELETE CASCADE
            )
        ''')
        conn.commit()
        conn.close()
    else:
        print(f"Database '{db_name}' found.")


if __name__ == "__main__":
    main()
