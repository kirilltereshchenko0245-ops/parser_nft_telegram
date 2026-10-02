import json, sqlite3, threading
from datetime import datetime

class Database:
    def __init__(self, path='bot_database.db'):
        self.path=path; self.lock=threading.RLock(); self.stop_flags=set(); self.init_db()
    def conn(self):
        c=sqlite3.connect(self.path, timeout=30); c.row_factory=sqlite3.Row; return c
    def init_db(self):
        c=self.conn(); q=c.execute
        q('''CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
          is_banned INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
          trial_searches_left INTEGER DEFAULT 25, total_searches INTEGER DEFAULT 0,
          total_found INTEGER DEFAULT 0, registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        q('''CREATE TABLE IF NOT EXISTS search_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, model TEXT,
          start_id INTEGER, end_id INTEGER, last_checked_id INTEGER DEFAULT 0,
          found_count INTEGER DEFAULT 0, checked_count INTEGER DEFAULT 0,
          status TEXT DEFAULT 'in_progress', started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          completed_at TIMESTAMP)''')
        q('''CREATE TABLE IF NOT EXISTS found_gifts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, search_id INTEGER, user_id INTEGER,
          gift_number INTEGER, model TEXT, pattern TEXT, backdrop TEXT, owner TEXT,
          username TEXT, user_id_owner INTEGER, price REAL, raw_json TEXT,
          found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(search_id, gift_number))''')
        q('''CREATE TABLE IF NOT EXISTS favorites(
          user_id INTEGER, gift_number INTEGER, model TEXT, username TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id,gift_number,model))''')
        # Safe migration from v3/v4.
        cols={r[1] for r in q('PRAGMA table_info(users)').fetchall()}
        for name, ddl in [('trial_searches_left','ALTER TABLE users ADD COLUMN trial_searches_left INTEGER DEFAULT 25'),('total_searches','ALTER TABLE users ADD COLUMN total_searches INTEGER DEFAULT 0'),('total_found','ALTER TABLE users ADD COLUMN total_found INTEGER DEFAULT 0')]:
            if name not in cols: q(ddl)
        c.commit(); c.close()
    def upsert_user(self,uid,username=None,first_name=None):
        c=self.conn(); c.execute('''INSERT INTO users(user_id,username,first_name) VALUES(?,?,?)
          ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,last_activity=CURRENT_TIMESTAMP''',(uid,username,first_name)); c.commit(); c.close()
    def get_user(self,uid):
        c=self.conn(); r=c.execute('SELECT * FROM users WHERE user_id=?',(uid,)).fetchone(); c.close(); return dict(r) if r else None
    def is_banned(self,uid): return bool((self.get_user(uid) or {}).get('is_banned'))
    def create_search(self,uid,model,start,end):
        c=self.conn(); cur=c.execute('INSERT INTO search_history(user_id,model,start_id,end_id,last_checked_id) VALUES(?,?,?,?,?)',(uid,model,start,end,start-1)); sid=cur.lastrowid
        c.execute('UPDATE users SET total_searches=total_searches+1 WHERE user_id=?',(uid,)); c.commit(); c.close(); return sid
    def update_search(self,sid,last,checked,found,status=None):
        c=self.conn()
        if status: c.execute('UPDATE search_history SET last_checked_id=?,checked_count=?,found_count=?,status=?,completed_at=CURRENT_TIMESTAMP WHERE id=?',(last,checked,found,status,sid))
        else: c.execute('UPDATE search_history SET last_checked_id=?,checked_count=?,found_count=? WHERE id=?',(last,checked,found,sid))
        c.commit(); c.close()
    def add_gift(self,sid,uid,g):
        raw=json.dumps(g.get('raw',{}),ensure_ascii=False)
        c=self.conn(); cur=c.execute('''INSERT OR IGNORE INTO found_gifts
          (search_id,user_id,gift_number,model,pattern,backdrop,owner,username,user_id_owner,price,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(sid,uid,g.get('giftNumber'),g.get('model'),g.get('pattern'),g.get('backdrop'),g.get('owner'),g.get('username'),g.get('userId'),(g.get('market') or {}).get('price'),raw))
        if cur.rowcount: c.execute('UPDATE users SET total_found=total_found+1 WHERE user_id=?',(uid,))
        c.commit(); c.close()
    def get_search(self,sid,uid=None):
        c=self.conn(); sql='SELECT * FROM search_history WHERE id=?'; args=[sid]
        if uid is not None: sql+=' AND user_id=?'; args.append(uid)
        r=c.execute(sql,args).fetchone(); c.close(); return dict(r) if r else None
    def get_gifts(self,sid):
        c=self.conn(); r=c.execute('SELECT * FROM found_gifts WHERE search_id=? ORDER BY gift_number',(sid,)).fetchall(); c.close(); return [dict(x) for x in r]
    def history(self,uid,limit=12):
        c=self.conn(); r=c.execute('SELECT * FROM search_history WHERE user_id=? ORDER BY id DESC LIMIT ?',(uid,limit)).fetchall(); c.close(); return [dict(x) for x in r]
    def add_favorite(self,uid,n,model,username):
        c=self.conn(); c.execute('INSERT OR IGNORE INTO favorites(user_id,gift_number,model,username) VALUES(?,?,?,?)',(uid,n,model,username)); c.commit(); c.close()
    def remove_favorite(self,uid,n,model):
        c=self.conn(); c.execute('DELETE FROM favorites WHERE user_id=? AND gift_number=? AND model=?',(uid,n,model)); c.commit(); c.close()
    def is_favorite(self,uid,n,model):
        c=self.conn(); r=c.execute('SELECT 1 FROM favorites WHERE user_id=? AND gift_number=? AND model=?',(uid,n,model)).fetchone(); c.close(); return bool(r)
    def favorites(self,uid,limit=50):
        c=self.conn(); r=c.execute('SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC LIMIT ?',(uid,limit)).fetchall(); c.close(); return [dict(x) for x in r]
    def consume_trial(self,uid,count):
        c=self.conn(); c.execute('UPDATE users SET trial_searches_left=MAX(0,trial_searches_left-?) WHERE user_id=?',(count,uid)); c.commit(); c.close()
    def stop(self,uid): self.stop_flags.add(uid)
    def clear_stop(self,uid): self.stop_flags.discard(uid)
    def should_stop(self,uid): return uid in self.stop_flags
    def set_ban(self,uid,value):
        c=self.conn(); c.execute('UPDATE users SET is_banned=? WHERE user_id=?',(int(value),uid)); c.commit(); c.close()
    def stats(self):
        c=self.conn(); u=c.execute('SELECT COUNT(*) users,COALESCE(SUM(is_premium),0) premium,COALESCE(SUM(is_banned),0) banned FROM users').fetchone(); s=c.execute('SELECT COUNT(*) searches,COALESCE(SUM(found_count),0) found FROM search_history').fetchone(); c.close(); return {**dict(u),**dict(s)}
