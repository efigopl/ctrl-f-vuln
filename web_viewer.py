from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)
DB_PATH = 'ctrl_f_vuln.db'


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
    hide_checked = request.args.get('hide_checked', 'off') == 'on'
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
            SELECT f.id, f.name, f.path, r.full_name as repo_name, r.stargazers_count as stars, r.html_url as repo_url, f.html_url as file_url, f.checked
            FROM Files f
            JOIN Repositories r ON f.repository_id = r.id
            JOIN Projects p ON p.name = ?
            JOIN Searches s ON s.project_id = p.id
            JOIN FileSearches fs ON fs.file_id = f.id AND fs.search_id = s.id
            WHERE r.stargazers_count >= ?
        '''
        params = [project, min_stars]
        if hide_checked:
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
    return render_template('index.html', projects=projects, project=project, min_stars=min_stars, files=files, page=page, total_pages=total_pages, hide_checked=hide_checked)

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
