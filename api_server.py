"""
用户注册API服务
Flask REST API - 用户注册与认证
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import hashlib
import secrets
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = 'users.db'

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT DEFAULT 'active'
        )
    ''')
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_api_key() -> str:
    """生成API密钥"""
    return secrets.token_urlsafe(32)

# 注册API
@app.route('/api/v1/register', methods=['POST'])
def register():
    """用户注册接口"""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': '请求体不能为空'}), 400
    
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    # 验证必填字段
    if not username or not email or not password:
        return jsonify({'error': '用户名、邮箱和密码不能为空'}), 400
    
    if len(password) < 6:
        return jsonify({'error': '密码长度至少6位'}), 400
    
    # 验证邮箱格式
    if '@' not in email:
        return jsonify({'error': '邮箱格式不正确'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查用户名是否存在
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'error': '用户名已存在'}), 409
        
        # 检查邮箱是否存在
        cursor.execute('SELECT id FROM users WHERE email = ?', (email,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'error': '邮箱已被注册'}), 409
        
        # 创建用户
        password_hash = hash_password(password)
        api_key = generate_api_key()
        created_at = datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO users (username, email, password_hash, api_key, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, email, password_hash, api_key, created_at))
        
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        
        return jsonify({
            'message': '注册成功',
            'user_id': user_id,
            'username': username,
            'api_key': api_key
        }), 201
        
    except Exception as e:
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500

# 获取用户信息API
@app.route('/api/v1/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    """获取用户信息"""
    api_key = request.headers.get('X-API-Key', '')
    
    if not api_key:
        return jsonify({'error': '缺少API密钥'}), 401
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, username, email, api_key, created_at, status
            FROM users WHERE id = ? AND api_key = ?
        ''', (user_id, api_key))
        
        user = cursor.fetchone()
        conn.close()
        
        if not user:
            return jsonify({'error': '用户不存在或API密钥无效'}), 404
        
        return jsonify({
            'id': user[0],
            'username': user[1],
            'email': user[2],
            'api_key': user[3],
            'created_at': user[4],
            'status': user[5]
        })
        
    except Exception as e:
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500

# 健康检查
@app.route('/health', methods=['GET'])
def health():
    """健康检查接口"""
    return jsonify({'status': 'healthy', 'service': 'user-registry-api'})

if __name__ == '__main__':
    init_db()
    print('用户注册API服务启动中...')
    app.run(host='0.0.0.0', port=5000, debug=True)
