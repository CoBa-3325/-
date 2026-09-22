"""Repository and transactional operations for the bot."""
from __future__ import annotations
import json, secrets, sqlite3, string, uuid
from datetime import datetime, timezone
from app.schemas.message import MessageRecord
from app.schemas.settings import ChatSettings

class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    # ---------- users / roles ----------
    def upsert_user(self, user_id, first_name='', last_name=''):
        now=self.now()
        self.connection.execute("""INSERT INTO users(user_id,first_name,last_name,role,subscription_start,subscription_end,is_unlimited_subscription,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name,last_name=excluded.last_name,updated_at=excluded.updated_at""",
            (user_id,first_name,last_name,'user',None,None,0,now,now))
        self.connection.commit()

    def get_role(self, user_id):
        row=self.connection.execute("SELECT role FROM users WHERE user_id=?",(user_id,)).fetchone()
        return row['role'] if row else 'user'

    def user_display_name(self,user_id):
        row=self.connection.execute("SELECT first_name,last_name FROM users WHERE user_id=?",(user_id,)).fetchone()
        if not row: return 'пользователь'
        return ' '.join(x for x in (row['first_name'],row['last_name']) if x).strip() or str(user_id)

    def set_role_if_current(self,user_id,current_role,new_role):
        cur=self.connection.execute("UPDATE users SET role=?,updated_at=? WHERE user_id=? AND role=?",(new_role,self.now(),user_id,current_role))
        self.connection.commit(); return cur.rowcount==1

    def list_users_by_role(self, role):
        return self.connection.execute("SELECT * FROM users WHERE role=? ORDER BY COALESCE(first_name,''),COALESCE(last_name,''),user_id",(role,)).fetchall()

    # ---------- chats / context ----------
    def upsert_chat(self,chat_id,chat_type,title=None):
        now=self.now()
        self.connection.execute("""INSERT INTO chats(chat_id,chat_type,title,created_at,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET chat_type=excluded.chat_type,title=COALESCE(excluded.title,chats.title),updated_at=excluded.updated_at""",(chat_id,chat_type,title,now,now)); self.connection.commit()

    def list_user_chats(self,user_id):
        return self.connection.execute("""SELECT DISTINCT c.chat_id,c.chat_type,c.title
            FROM chats c JOIN messages m ON m.chat_id=c.chat_id WHERE m.user_id=? AND c.chat_type='chat'
            ORDER BY c.updated_at DESC""",(user_id,)).fetchall()

    def save_message(self,message:MessageRecord):
        cur=self.connection.execute("INSERT OR IGNORE INTO messages(max_message_id,chat_id,user_id,user_name,text,source,timestamp,reply_to) VALUES(?,?,?,?,?,?,?,?)",
            (message.max_message_id,message.chat_id,message.user_id,message.user_name,message.text,message.source,message.timestamp.isoformat(),message.reply_to))
        self.connection.commit(); return cur.rowcount>0

    def get_messages(self,chat_id,start,end):
        return self.connection.execute("SELECT * FROM messages WHERE chat_id=? AND timestamp>=? AND timestamp<? ORDER BY timestamp ASC",(chat_id,start.isoformat(),end.isoformat())).fetchall()

    def save_summary_run(self,chat_id,start,end,text):
        self.connection.execute("INSERT INTO summary_runs(chat_id,period_start,period_end,created_at,summary_text) VALUES(?,?,?,?,?)",(chat_id,start.isoformat(),end.isoformat(),self.now(),text)); self.connection.commit()

    # ---------- settings ----------
    def get_settings(self,chat_id):
        row=self.connection.execute("SELECT * FROM chat_settings WHERE chat_id=?",(chat_id,)).fetchone()
        if row is None:return ChatSettings(chat_id=chat_id)
        return ChatSettings(chat_id=chat_id,enabled=bool(row['enabled']),interval_type=row['interval_type'],custom_days=row['custom_days'],summary_hour=row['summary_hour'],timezone=row['timezone'])

    def save_settings(self,s):
        self.connection.execute("""INSERT INTO chat_settings(chat_id,enabled,interval_type,custom_days,summary_hour,timezone) VALUES(?,?,?,?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled,interval_type=excluded.interval_type,custom_days=excluded.custom_days,summary_hour=excluded.summary_hour,timezone=excluded.timezone""",(s.chat_id,int(s.enabled),s.interval_type,s.custom_days,s.summary_hour,s.timezone)); self.connection.commit()

    # ---------- tokens ----------
    def ensure_trial_grant(self,user_id,amount):
        if amount<=0:return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row=self.connection.execute("SELECT 1 FROM token_grants WHERE user_id=? AND token_type='trial' LIMIT 1",(user_id,)).fetchone()
            if row is None:
                now=self.now(); self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(user_id,amount,'trial',None,amount,f'trial:user:{user_id}',now))
            self.connection.commit()
        except Exception:
            self.connection.rollback(); raise

    def get_token_balance(self,user_id):
        now=self.now()
        row=self.connection.execute("SELECT COALESCE(SUM(remaining_amount),0) AS n FROM token_grants WHERE user_id=? AND remaining_amount>0 AND (expires_at IS NULL OR expires_at>?)",(user_id,now)).fetchone()
        return int(row['n'] or 0)

    def add_token_grant(self,user_id,amount,token_type,expires_at=None,source_id=None):
        if amount<=0: raise ValueError('token amount must be positive')
        now=self.now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if source_id and self.connection.execute("SELECT 1 FROM token_grants WHERE source_id=?",(source_id,)).fetchone():
                self.connection.rollback(); return False
            self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(user_id,amount,token_type,expires_at,amount,source_id,now))
            self.connection.commit(); return True
        except Exception:
            self.connection.rollback(); raise

    def add_subscription_tokens(self,user_id,months,source_id):
        return self.add_token_grant(user_id,months*30,'subscription',None,source_id)

    def deduct_token(self,user_id,operation_id):
        """Atomically select the preferred non-expired grant and decrement it."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.connection.execute("SELECT 1 FROM token_operations WHERE operation_id=?",(operation_id,)).fetchone():
                self.connection.rollback(); return False
            now=self.now()
            row=self.connection.execute("""SELECT id FROM token_grants WHERE user_id=? AND remaining_amount>0
                AND (expires_at IS NULL OR expires_at>?)
                ORDER BY CASE token_type WHEN 'subscription' THEN 1 WHEN 'promo' THEN 2 WHEN 'trial' THEN 3 WHEN 'purchased' THEN 4 ELSE 9 END,
                         CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END, expires_at, id""",(user_id,now)).fetchone()
            if row is None:
                self.connection.rollback(); return False
            grant_id=row['id']
            self.connection.execute("UPDATE token_grants SET remaining_amount=remaining_amount-1 WHERE id=? AND remaining_amount>0",(grant_id,))
            self.connection.execute("INSERT INTO token_operations(operation_id,user_id,grant_id,delta,reason,created_at) VALUES(?,?,?,?,?,?)",(operation_id,user_id,grant_id,-1,'llm_request',now))
            self.connection.commit(); return True
        except Exception:
            self.connection.rollback(); raise

    def refund_token(self,user_id,operation_id):
        """Return exactly the grant that was consumed; idempotent by operation id."""
        refund_id=f'refund:{operation_id}'
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.connection.execute("SELECT 1 FROM token_operations WHERE operation_id=?",(refund_id,)).fetchone():
                self.connection.rollback(); return False
            row=self.connection.execute("SELECT grant_id FROM token_operations WHERE operation_id=? AND delta=-1",(operation_id,)).fetchone()
            if not row or row['grant_id'] is None:
                self.connection.rollback(); return False
            self.connection.execute("UPDATE token_grants SET remaining_amount=remaining_amount+1 WHERE id=?",(row['grant_id'],))
            self.connection.execute("INSERT INTO token_operations(operation_id,user_id,grant_id,delta,reason,created_at) SELECT ?,user_id,grant_id,1,'llm_refund',? FROM token_operations WHERE operation_id=?",(refund_id,self.now(),operation_id))
            self.connection.commit(); return True
        except Exception:
            self.connection.rollback(); raise

    # ---------- subscriptions ----------
    def get_subscription(self,user_id):return self.connection.execute("SELECT * FROM subscriptions WHERE user_id=?",(user_id,)).fetchone()
    def has_unlimited_promo_subscription(self,user_id):
        row=self.connection.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND is_unlimited_subscription=1 AND source='promo'",(user_id,)).fetchone(); return row is not None
    def get_subscription_status(self,user_id,now=None):
        now=now or datetime.now(timezone.utc); row=self.connection.execute("SELECT ends_at,is_unlimited_subscription FROM subscriptions WHERE user_id=?",(user_id,)).fetchone()
        if not row or row['is_unlimited_subscription'] or not row['ends_at']: return None
        end=datetime.fromisoformat(row['ends_at']); return end if end>now else None
    def upsert_subscription(self,user_id,start,end,is_unlimited=False,source='paid'):
        now=self.now(); stored_end = end.isoformat() if end else ('9999-12-31T23:59:59+00:00' if is_unlimited else None)
        self.connection.execute("""INSERT INTO subscriptions(user_id,started_at,ends_at,is_unlimited_subscription,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET started_at=excluded.started_at,ends_at=excluded.ends_at,is_unlimited_subscription=excluded.is_unlimited_subscription,source=excluded.source,updated_at=excluded.updated_at""",(user_id,start.isoformat(),stored_end,int(is_unlimited),source,now,now)); self.connection.execute("UPDATE users SET subscription_start=?,subscription_end=?,is_unlimited_subscription=?,updated_at=? WHERE user_id=?",(start.isoformat(),stored_end,int(is_unlimited),now,user_id)); self.connection.commit()

    def list_subscriptions_expiring(self,now,limit):
        return self.connection.execute("SELECT * FROM subscriptions WHERE is_unlimited_subscription=0 AND ends_at>? AND ends_at<=?",(now.isoformat(),limit.isoformat())).fetchall()

    def mark_subscription_warning(self,user_id,ends_at):
        cur=self.connection.execute("UPDATE subscriptions SET warning_sent_for=?,updated_at=? WHERE user_id=? AND warning_sent_for IS NOT ?",(ends_at,self.now(),user_id,ends_at));self.connection.commit();return cur.rowcount==1

    # ---------- orders ----------
    def create_order(self,order):
        self.connection.execute("""INSERT INTO orders(order_id,user_id,payment_id,product_type,tariff,chat_id,token_amount,subscription_months,original_amount,final_amount,status,created_at,updated_at,paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",order); self.connection.commit()
    def get_order(self,order_id):return self.connection.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()
    def get_order_by_payment(self,payment_id):return self.connection.execute("SELECT * FROM orders WHERE payment_id=?",(payment_id,)).fetchone()
    def set_payment_id(self,order_id,payment_id):self.connection.execute("UPDATE orders SET payment_id=?,updated_at=? WHERE order_id=?",(payment_id,self.now(),order_id));self.connection.commit()
    def set_order_status(self,order_id,status):self.connection.execute("UPDATE orders SET status=?,updated_at=? WHERE order_id=?",(status,self.now(),order_id));self.connection.commit()
    def fulfill_order(self,order_id,*,token_amount=0,subscription_start=None,subscription_end=None):
        now=self.now(); self.connection.execute("BEGIN IMMEDIATE")
        try:
            row=self.connection.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()
            if not row:self.connection.rollback();return False
            if row['status']=='paid':self.connection.rollback();return True
            if row['status']!='pending':self.connection.rollback();return False
            self.connection.execute("UPDATE orders SET status='paid',paid_at=?,updated_at=? WHERE order_id=? AND status='pending'",(now,now,order_id))
            if row['product_type']=='tokens':
                self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(row['user_id'],token_amount,'purchased',None,token_amount,f'payment:{order_id}',now))
            else:
                self.connection.execute("""INSERT INTO subscriptions(user_id,started_at,ends_at,is_unlimited_subscription,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET started_at=excluded.started_at,ends_at=excluded.ends_at,is_unlimited_subscription=0,source='paid',updated_at=excluded.updated_at""",(row['user_id'],subscription_start.isoformat(),subscription_end.isoformat(),0,'paid',now,now))
                self.connection.execute("UPDATE users SET subscription_start=?,subscription_end=?,is_unlimited_subscription=0,updated_at=? WHERE user_id=?",(subscription_start.isoformat(),subscription_end.isoformat(),now,row['user_id']))
                self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(row['user_id'],row['subscription_months']*30,'subscription',subscription_end.isoformat(),row['subscription_months']*30,f'payment:{order_id}:subscription_tokens',now))
            self.connection.commit();return True
        except Exception:
            self.connection.rollback();raise

    # ---------- cooldown ----------
    def get_cooldown_until(self,chat_id):
        row=self.connection.execute("SELECT cooldown_until FROM chat_cooldowns WHERE chat_id=?",(chat_id,)).fetchone()
        return datetime.fromisoformat(row['cooldown_until']) if row and row['cooldown_until'] else None

    def acquire_llm_slot(self,chat_id):
        now=datetime.now(timezone.utc); now_s=now.isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row=self.connection.execute("SELECT cooldown_until,in_flight FROM chat_cooldowns WHERE chat_id=?",(chat_id,)).fetchone()
            if row and row['in_flight']:
                self.connection.rollback();return False
            if row and row['cooldown_until'] and datetime.fromisoformat(row['cooldown_until'])>now:
                self.connection.rollback();return False
            self.connection.execute("INSERT INTO chat_cooldowns(chat_id,cooldown_until,in_flight,updated_at) VALUES(?,?,1,?) ON CONFLICT(chat_id) DO UPDATE SET in_flight=1,updated_at=excluded.updated_at",(chat_id,None,now_s))
            self.connection.commit();return True
        except Exception:self.connection.rollback();raise

    def complete_llm(self,chat_id,cooldown_until):
        now=self.now();self.connection.execute("UPDATE chat_cooldowns SET cooldown_until=?,in_flight=0,updated_at=? WHERE chat_id=?",(cooldown_until.isoformat(),now,chat_id));self.connection.commit()

    def release_llm(self,chat_id):
        self.connection.execute("UPDATE chat_cooldowns SET in_flight=0,updated_at=? WHERE chat_id=?",(self.now(),chat_id));self.connection.commit()

    def set_cooldown(self,chat_id,until):
        self.complete_llm(chat_id,until);return True

    # ---------- UI state ----------
    def set_user_state(self,user_id,state,data):
        self.connection.execute("INSERT INTO user_states(user_id,state,data_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,data_json=excluded.data_json,updated_at=excluded.updated_at",(user_id,state,json.dumps(data,ensure_ascii=False),self.now()));self.connection.commit()
    def get_user_state(self,user_id):
        row=self.connection.execute("SELECT * FROM user_states WHERE user_id=?",(user_id,)).fetchone()
        return (row['state'],json.loads(row['data_json'])) if row else None
    def clear_user_state(self,user_id):self.connection.execute("DELETE FROM user_states WHERE user_id=?",(user_id,));self.connection.commit()

    # ---------- promo codes ----------
    @staticmethod
    def normalize_code(code):return ''.join(str(code).split()).upper()
    def create_promo(self,*,code,usage_type,reward_type,created_by,max_uses=None,token_amount=0,subscription_months=0,is_unlimited_subscription=False,admin_role=None,expires_at=None):
        code=self.normalize_code(code)
        if not code: raise ValueError('empty promo code')
        now=self.now()
        self.connection.execute("INSERT INTO promo_codes(code,usage_type,max_uses,used_count,reward_type,token_amount,subscription_months,is_unlimited_subscription,admin_role,expires_at,is_active,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code,usage_type,max_uses,0,reward_type,token_amount,subscription_months,int(is_unlimited_subscription),admin_role,expires_at,1,created_by,now,now));self.connection.commit();return self.get_promo(code)
    def get_promo(self,code):return self.connection.execute("SELECT * FROM promo_codes WHERE code=?",(self.normalize_code(code),)).fetchone()
    def list_promo_usages(self,user_id):return self.connection.execute("SELECT * FROM promo_code_usages WHERE user_id=? ORDER BY used_at DESC",(user_id,)).fetchall()
    def redeem_promo(self,user_id,code,role_service=None,subscription_service=None):
        """Redeem a promo atomically, including use-limit increment and reward."""
        code=self.normalize_code(code); now=self.now(); self.connection.execute('BEGIN IMMEDIATE')
        try:
            p=self.connection.execute("SELECT * FROM promo_codes WHERE code=? AND is_active=1",(code,)).fetchone()
            if not p or (p['expires_at'] and p['expires_at']<=now):self.connection.rollback();return None,'Промокод недействителен или уже использован'
            if p['usage_type']=='once' and p['used_count']>=1:self.connection.rollback();return None,'Промокод недействителен или уже использован'
            if p['usage_type']=='limited' and p['used_count']>=p['max_uses']:self.connection.rollback();return None,'Промокод недействителен или уже использован'
            if self.connection.execute("SELECT 1 FROM promo_code_usages WHERE promo_code_id=? AND user_id=?",(p['id'],user_id)).fetchone():self.connection.rollback();return None,'Промокод недействителен или уже использован'
            if p['reward_type']=='role':
                target=p['admin_role']; current=self.get_role(user_id)
                if target=='creator' and current!='user':self.connection.rollback();return None,'Промокод недействителен или уже использован'
                if target=='admin' and current!='user':self.connection.rollback();return None,'Промокод недействителен или уже использован'
                self.connection.execute("UPDATE users SET role=?,updated_at=? WHERE user_id=? AND role='user'",(target,now,user_id))
                if self.connection.execute("SELECT changes()").fetchone()[0]!=1:self.connection.rollback();return None,'Промокод недействителен или уже использован'
                amount=0
            elif p['reward_type']=='tokens':
                self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(user_id,p['token_amount'],'promo',p['expires_at'],p['token_amount'],f"promo:{p['id']}:{user_id}",now)); amount=p['token_amount']
            else:
                if p['is_unlimited_subscription']:
                    self.connection.execute("INSERT INTO subscriptions(user_id,started_at,ends_at,is_unlimited_subscription,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET started_at=excluded.started_at,ends_at=NULL,is_unlimited_subscription=1,source='promo',updated_at=excluded.updated_at",(user_id,now,'9999-12-31T23:59:59+00:00',1,'promo',now,now))
                else:
                    row=self.connection.execute("SELECT * FROM subscriptions WHERE user_id=?",(user_id,)).fetchone()
                    from datetime import timedelta
                    current_end=datetime.fromisoformat(row['ends_at']) if row and row['ends_at'] and not row['is_unlimited_subscription'] else datetime.fromisoformat(now)
                    base=current_end if current_end>datetime.fromisoformat(now) else datetime.fromisoformat(now)
                    end=base+timedelta(days=30*p['subscription_months'])
                    self.connection.execute("INSERT INTO subscriptions(user_id,started_at,ends_at,is_unlimited_subscription,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET started_at=excluded.started_at,ends_at=excluded.ends_at,is_unlimited_subscription=0,source='promo',updated_at=excluded.updated_at",(user_id,now,end.isoformat(),0,'promo',now,now))
                    self.connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",(user_id,p['subscription_months']*30,'subscription',end.isoformat(),p['subscription_months']*30,f"promo:{p['id']}:{user_id}:subscription_tokens",now))
                amount=p['subscription_months']
            self.connection.execute("INSERT INTO promo_code_usages(promo_code_id,user_id,reward_type,reward_amount,used_at) VALUES(?,?,?,?,?)",(p['id'],user_id,p['reward_type'],amount,now))
            self.connection.execute("UPDATE promo_codes SET used_count=used_count+1,updated_at=? WHERE id=?",(now,p['id']))
            self.connection.commit(); return p,None
        except Exception:
            self.connection.rollback(); raise

    def ensure_initial_admin_promo(self):
        row=self.connection.execute("SELECT * FROM promo_codes WHERE code LIKE 'INITIAL-ADMIN-%' LIMIT 1").fetchone()
        if row:return None
        alphabet=string.ascii_uppercase+string.digits
        while True:
            code='INITIAL-ADMIN-'+''.join(secrets.choice(alphabet) for _ in range(24))
            try:
                return self.create_promo(code=code,usage_type='once',reward_type='role',admin_role='admin',created_by=0)
            except sqlite3.IntegrityError:
                continue

    # ---------- polls ----------
    def record_poll(self,poll_id,chat_id,question,data,total_votes):
        self.connection.execute("INSERT INTO poll_context(poll_id,chat_id,question,data_json,total_votes,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(poll_id) DO UPDATE SET question=excluded.question,data_json=excluded.data_json,total_votes=excluded.total_votes,updated_at=excluded.updated_at",(str(poll_id),chat_id,question,json.dumps(data,ensure_ascii=False),total_votes,self.now()));self.connection.commit()
