import os
import subprocess
from flask import Flask, render_template, request, redirect, url_for
import sqlite3

# Global constants
EDITOR_PATH = r"code.cmd"  # Path to VS Code executable
PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")

app = Flask(__name__)
DB_PATH = 'ctrl_f_vuln.db'

# Create projects directory if it doesn't exist
if not os.path.exists(PROJECTS_DIR):
    os.makedirs(PROJECTS_DIR)


@app.route('/', methods=['GET'])
def index():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Get all projects
    cursor.execute('SELECT name FROM Projects')
    projects = [row['name'] for row in cursor.fetchall()]
    project = request.args.get('project', projects[0] if projects else None)
    min_stars = int(request.args.get('min_stars', 0))
    files = []
    page = int(request.args.get('page', 1))
    per_page = 100
    offset = (page - 1) * per_page
    total_files = 0
    show_checked = request.args.get('show_checked', 'off') == 'on'
    if project:
        count_query = '''
            SELECT COUNT(DISTINCT f.id)
            FROM Files f
            JOIN Repositories r ON f.repository_id = r.id
            JOIN Projects p ON p.name = ?
            JOIN Searches s ON s.project_id = p.id
            JOIN FileSearches fs ON fs.file_id = f.id AND fs.search_id = s.id
            WHERE r.stargazers_count >= ?
        '''
        file_query = '''
            SELECT f.id, f.name, f.path, r.full_name as repo_name, r.stargazers_count as stars, r.html_url as repo_url, f.html_url as file_url, f.checked, r.id as repo_id
            FROM Files f
            JOIN Repositories r ON f.repository_id = r.id
            JOIN Projects p ON p.name = ?
            JOIN Searches s ON s.project_id = p.id
            JOIN FileSearches fs ON fs.file_id = f.id AND fs.search_id = s.id
            WHERE r.stargazers_count >= ?
        '''
        params = [project, min_stars]
        if not show_checked:
            count_query += ' AND f.checked=0'
            file_query += ' AND f.checked=0'
        file_query += '''
            GROUP BY f.id
            ORDER BY r.stargazers_count DESC
            LIMIT ? OFFSET ?
        '''
        params += [per_page, offset]
        cursor.execute(count_query, (project, min_stars))
        total_files = cursor.fetchone()[0]
        cursor.execute(file_query, params)
        files = [dict(row) for row in cursor.fetchall()]
    total_pages = (total_files + per_page - 1) // per_page if total_files else 1
    conn.close()
    return render_template('index.html', projects=projects, project=project, min_stars=min_stars, files=files, page=page, total_pages=total_pages, show_checked=show_checked)

# Repository details view
@app.route('/repo/<int:repo_id>', methods=['GET'])
def repo_details(repo_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get repository and project details
    cursor.execute('''
        SELECT r.full_name, r.html_url, p.name as project_name
        FROM Repositories r
        JOIN Files f ON f.repository_id = r.id
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON s.id = fs.search_id
        JOIN Projects p ON p.id = s.project_id
        WHERE r.id = ?
        GROUP BY r.id
    ''', (repo_id,))
    repo = cursor.fetchone()
    if not repo:
        conn.close()
        return "Repository not found", 404
        
    # Check if repository is already cloned in the project directory
    project_dir = os.path.join(PROJECTS_DIR, repo['project_name'])
    repo_dir = os.path.join(project_dir, repo['full_name'].split('/')[-1])
    is_cloned = os.path.exists(repo_dir)
        
    cursor.execute('''
        SELECT f.id, f.name, f.path, f.html_url as file_url, f.checked,
               GROUP_CONCAT(DISTINCT s.query) as search_queries
        FROM Files f
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON s.id = fs.search_id
        WHERE f.repository_id=?
        GROUP BY f.id
        ORDER BY f.name
    ''', (repo_id,))
    files = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return render_template('repo_details.html', 
                         repo_id=repo_id, 
                         repo_name=repo['full_name'], 
                         repo_url=repo['html_url'], 
                         files=files,
                         is_cloned=is_cloned,
                         repo_dir=repo_dir if is_cloned else None)

# Mark all files in a repository as checked
@app.route('/repo/<int:repo_id>/check_all', methods=['POST'])
def check_all_files(repo_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE Files SET checked=1 WHERE repository_id=?', (repo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('repo_details', repo_id=repo_id))

# Clone repository and open in VS Code
@app.route('/repo/<int:repo_id>/clone', methods=['POST'])
def clone_and_open(repo_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get repository and project details
    cursor.execute('''
        SELECT r.full_name, r.html_url, p.name as project_name
        FROM Repositories r
        JOIN Files f ON f.repository_id = r.id
        JOIN FileSearches fs ON fs.file_id = f.id
        JOIN Searches s ON s.id = fs.search_id
        JOIN Projects p ON p.id = s.project_id
        WHERE r.id = ?
        GROUP BY r.id
    ''', (repo_id,))
    repo = cursor.fetchone()
    if not repo:
        conn.close()
        return "Repository not found", 404
    
    # Create project directory if it doesn't exist
    project_dir = os.path.join(PROJECTS_DIR, repo['project_name'])
    if not os.path.exists(project_dir):
        os.makedirs(project_dir)
    
    # Create directory for the repository within the project directory
    repo_dir = os.path.join(project_dir, repo['full_name'].split('/')[-1])
    
    try:
        # Clone the repository if it doesn't exist
        if not os.path.exists(repo_dir):
            subprocess.run(['git', 'clone', repo['html_url'], repo_dir], check=True)
        
        # Open the repository in VS Code
        subprocess.Popen([EDITOR_PATH, repo_dir])
        
        conn.close()
        return redirect(url_for('repo_details', repo_id=repo_id))
    except subprocess.CalledProcessError as e:
        conn.close()
        return f"Error cloning repository: {str(e)}", 500
    except Exception as e:
        conn.close()
        return f"Error: {str(e)}", 500

@app.route('/check/<int:file_id>', methods=['POST'])
def mark_checked(file_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('UPDATE Files SET checked=1 WHERE id=?', (file_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
