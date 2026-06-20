import os
import csv
import sys
import math
from io import StringIO
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, Response, jsonify
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cambia-esta-clave-123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///entrenamiento.db').replace('postgres://', 'postgresql://')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'basic'

# ─── Modelos ────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    sessions = db.relationship('TrainingSession', backref='user', lazy=True,
                                order_by='TrainingSession.date.desc()')
    body_weights = db.relationship('BodyWeight', backref='user', lazy=True,
                                    order_by='BodyWeight.date.desc()')
    custom_exercises = db.relationship('CustomExercise', backref='user', lazy=True)
    player_xp = db.relationship('PlayerXP', uselist=False, backref='user')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)


class TrainingSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    duration_minutes = db.Column(db.Integer)
    notes = db.Column(db.Text)
    exercises = db.relationship('ExerciseLog', backref='session', lazy=True,
                                 order_by='ExerciseLog.id')
    comments = db.relationship('Comment', backref='session', lazy=True,
                                order_by='Comment.created_at')

    __table_args__ = (db.UniqueConstraint('user_id', 'date'),)


class ExerciseLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('training_session.id'),
                           nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(20))
    sets = db.Column(db.Integer)
    reps = db.Column(db.Integer)
    weight_kg = db.Column(db.Float)
    distance_km = db.Column(db.Float)
    time_minutes = db.Column(db.Float)

    def volume(self):
        if self.sets and self.reps and self.weight_kg:
            return self.sets * self.reps * self.weight_kg
        return 0


class ExerciseTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(20), nullable=False)


class BodyWeight(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    weight_kg = db.Column(db.Float, nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'date'),)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('training_session.id'),
                           nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    author = db.relationship('User', lazy=True)


class CustomExercise(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(20), default='free')


# ─── XP y Rangos ────────────────────────────────────────────────────

class PlayerXP(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=0)
    rank = db.Column(db.String(20), default='bronze')


RANK_CONFIG = [
    ('bronze',  'Bronce',    '#cd7f32', 0),
    ('silver',  'Plata',     '#c0c0c0', 5),
    ('gold',    'Oro',       '#ffd700', 15),
    ('platinum','Platino',   '#e5e4e2', 30),
    ('emerald', 'Esmeralda', '#50c878', 50),
    ('ruby',    'Rubí',      '#e0115f', 75),
]


def xp_for_level(level):
    return 5 * level * (level + 1)


def level_from_xp(total_xp):
    return int((-1 + math.sqrt(1 + 4 * total_xp / 5)) / 2)


def xp_for_exercises(session):
    xp = 0
    for e in session.exercises:
        xp += 10  # base per exercise
        if e.weight_kg:
            xp += int(e.weight_kg / 10)  # +1 cada 10kg
        if e.distance_km:
            xp += int(e.distance_km * 2)  # +2 por km
        if e.time_minutes and not e.distance_km:
            xp += int(e.time_minutes / 5)  # +1 cada 5 min (solo si no es cardio con distancia)
    return xp


def recalculate_xp(user_id):
    sessions = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date).all()
    total_xp = 0
    streak_count = 0
    prev_date = None
    for s in sessions:
        if prev_date is not None:
            diff = (s.date - prev_date).days
            if diff == 1:
                streak_count += 1
            elif diff > 1:
                streak_count = 0
        else:
            streak_count = 0
        xp_gain = 200 if streak_count >= 2 else 100
        xp_gain += xp_for_exercises(s)
        total_xp += xp_gain
        prev_date = s.date
    level = level_from_xp(total_xp)
    pxp = PlayerXP.query.get(user_id)
    if pxp:
        pxp.xp = total_xp
        pxp.level = level
    else:
        pxp = PlayerXP(user_id=user_id, xp=total_xp, level=level)
        db.session.add(pxp)
    return pxp


def calculate_rank(user_id, level=None, total_volume=None):
    if level is None:
        pxp = PlayerXP.query.get(user_id)
        level = pxp.level if pxp else 0
    if total_volume is None:
        total_volume = db.session.query(func.sum(
            ExerciseLog.sets * ExerciseLog.reps * ExerciseLog.weight_kg
        )).join(TrainingSession).filter(
            TrainingSession.user_id == user_id
        ).scalar() or 0
    rank_score = level + (total_volume / 50000)
    for rid, rname, rcolor, threshold in reversed(RANK_CONFIG):
        if rank_score >= threshold:
            return rid
    return 'bronze'


def get_rank_config(rank_id):
    for rid, rname, rcolor, _ in RANK_CONFIG:
        if rid == rank_id:
            return {'id': rid, 'name': rname, 'color': rcolor}
    return {'id': 'bronze', 'name': 'Bronce', 'color': '#cd7f32'}


def recalculate_all_xp():
    for user in User.query.all():
        try:
            pxp = PlayerXP.query.get(user.id)
            if pxp:
                db.session.delete(pxp)
            recalculate_xp(user.id)
        except Exception as e:
            print(f'Error recalculating XP for {user.username}: {e}', file=sys.stderr)
    db.session.commit()
    for user in User.query.all():
        pxp = PlayerXP.query.get(user.id)
        if pxp:
            vol = db.session.query(func.sum(
                ExerciseLog.sets * ExerciseLog.reps * ExerciseLog.weight_kg
            )).join(TrainingSession).filter(
                TrainingSession.user_id == user.id
            ).scalar() or 0
            pxp.rank = calculate_rank(user.id, pxp.level, vol)
    db.session.commit()


# ─── Ejercicios predefinidos ────────────────────────────────────────

PREDEFINED = [
    ('Press banca', 'gym'), ('Press inclinado', 'gym'),
    ('Press militar', 'gym'), ('Press francés', 'gym'),
    ('Sentadilla', 'gym'), ('Peso muerto', 'gym'),
    ('Prensa', 'gym'), ('Zancadas', 'gym'),
    ('Curl bíceps', 'gym'), ('Curl martillo', 'gym'),
    ('Dominadas', 'gym'), ('Remo con barra', 'gym'),
    ('Jalón al pecho', 'gym'), ('Remo en máquina', 'gym'),
    ('Elevaciones laterales', 'gym'), ('Fondos', 'gym'),
    ('Aperturas', 'gym'), ('Face pull', 'gym'),
    ('Peso muerto rumano', 'gym'), ('Hip thrust', 'gym'),
    ('Correr', 'cardio'), ('Bicicleta', 'cardio'),
    ('Natación', 'cardio'), ('Senderismo', 'cardio'),
    ('Elíptica', 'cardio'), ('Cuerda', 'cardio'),
    ('Remo máquina cardio', 'cardio'),
    ('Calistenia', 'free'), ('CrossFit', 'free'),
    ('Yoga', 'free'), ('Estiramientos', 'free'),
    ('Funcional', 'free'),
]


# ─── Helpers ────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def week_range():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def month_range(year, month):
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


def get_sessions_in_range(user_id, start, end):
    return TrainingSession.query.filter(
        TrainingSession.user_id == user_id,
        TrainingSession.date >= start,
        TrainingSession.date <= end
    ).all()


def get_streak(user_id):
    sessions = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date.desc()).all()
    if not sessions:
        return 0
    streak = 0
    today = date.today()
    check = today
    for session in sessions:
        if session.date == check:
            streak += 1
            check -= timedelta(days=1)
        elif session.date < check and streak == 0:
            if today - session.date > timedelta(days=1):
                break
            check = session.date - timedelta(days=1)
            streak += 1
        else:
            break
    return streak


def get_weekly_volume(user_id, monday, sunday):
    sessions = get_sessions_in_range(user_id, monday, sunday)
    total = 0
    for s in sessions:
        for e in s.exercises:
            total += e.volume()
    return total


def all_users_week_progress():
    monday, sunday = week_range()
    users = User.query.all()
    multa = get_multa()
    results = []
    for user in users:
        sessions = get_sessions_in_range(user.id, monday, sunday)
        trained_dates = [s.date for s in sessions]
        trained_days = len(trained_dates)
        penalty = 0
        if trained_days < 5:
            penalty = (5 - trained_days) * multa
        results.append({
            'user': user,
            'trained_days': trained_days,
            'days_rested': 7 - trained_days,
            'penalty': penalty,
            'trained_dates': trained_dates,
            'cumple': trained_days >= 5,
            'streak': get_streak(user.id),
        })
    return results, monday, sunday, multa


def seed_exercises():
    if ExerciseTemplate.query.first():
        return
    for name, cat in PREDEFINED:
        db.session.add(ExerciseTemplate(name=name, category=cat))
    db.session.commit()


def migrate_db():
    if os.environ.get('DATABASE_URL'):
        try:
            db.session.execute(db.text('ALTER TABLE "user" RENAME TO users'))
            db.session.commit()
        except Exception:
            pass
    db.create_all()
    for col in ['is_admin']:
        for tbl in ['users']:
            try:
                db.session.execute(db.text(f'SELECT {col} FROM {tbl} LIMIT 1'))
                break
            except Exception:
                try:
                    db.session.execute(db.text(f'ALTER TABLE {tbl} ADD COLUMN {col} BOOLEAN DEFAULT 0'))
                    db.session.commit()
                    break
                except Exception:
                    pass


def get_multa():
    s = Setting.query.get('multa_por_dia')
    try:
        if s:
            return int(s.value)
        return int(os.environ.get('MULTA_POR_DIA', 50))
    except (ValueError, TypeError):
        Setting.query.filter_by(key='multa_por_dia').delete()
        db.session.commit()
        return 50


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Acceso denegado')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ─── Auth ───────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash('Completa todos los campos')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('El usuario ya existe')
            return render_template('register.html')
        user = User(username=username)
        user.set_password(password)
        admin_user = os.environ.get('ADMIN_USERNAME', '').lower()
        if admin_user and username.lower() == admin_user:
            user.is_admin = True
        elif not admin_user and User.query.count() == 0:
            user.is_admin = True
        db.session.add(user)
        db.session.commit()
        flash('Registrado correctamente. Inicia sesión.')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            admin_user = os.environ.get('ADMIN_USERNAME', '').lower()
            if admin_user and username.lower() == admin_user and not user.is_admin:
                user.is_admin = True
                db.session.commit()
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── Dashboard ──────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    monday, sunday = week_range()
    sessions = get_sessions_in_range(current_user.id, monday, sunday)
    trained_dates = [s.date for s in sessions]
    trained_count = len(trained_dates)
    multa = get_multa()
    penalty = 0
    if trained_count < 5:
        penalty = (5 - trained_count) * multa
    today_session = TrainingSession.query.filter_by(
        user_id=current_user.id, date=today).first()
    week_days = [(monday + timedelta(days=i)) for i in range(7)]
    day_names = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']
    streak = get_streak(current_user.id)
    weekly_volume = get_weekly_volume(current_user.id, monday, sunday)
    last_weight = BodyWeight.query.filter_by(user_id=current_user.id)\
        .order_by(BodyWeight.date.desc()).first()
    pxp = PlayerXP.query.get(current_user.id)
    rank_info = get_rank_config(pxp.rank if pxp else 'bronze')
    xp_current = pxp.xp if pxp else 0
    xp_level = pxp.level if pxp else 0
    xp_next = xp_for_level(xp_level + 1)
    xp_prev = xp_for_level(xp_level)
    xp_progress = xp_current - xp_prev
    xp_needed = xp_next - xp_prev
    xp_pct = (xp_progress / xp_needed * 100) if xp_needed > 0 else 0
    return render_template('dashboard.html',
        monday=monday, sunday=sunday, today=today,
        trained_count=trained_count, trained_dates=trained_dates,
        today_session=today_session, penalty=penalty,
        cumple=trained_count >= 5, week_days=week_days,
        day_names=day_names, streak=streak,
        weekly_volume=weekly_volume, last_weight=last_weight,
        xp_data={'rank': rank_info, 'xp': xp_current, 'level': xp_level,
                 'xp_next': xp_next, 'xp_progress': xp_progress,
                 'xp_needed': xp_needed, 'xp_pct': xp_pct})


# ─── Registrar sesión ───────────────────────────────────────────────

@app.route('/registrar', methods=['GET', 'POST'])
@login_required
def registrar():
    if request.method == 'POST':
        try:
            session_date = datetime.strptime(
                request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            session_date = date.today()
        existing = TrainingSession.query.filter_by(
            user_id=current_user.id, date=session_date).first()
        if existing:
            flash('Ya tienes un entrenamiento registrado ese día')
            return redirect(url_for('ver_sesion', sesion_id=existing.id))
        duration = request.form.get('duration_minutes', type=int)
        notes = request.form.get('notes', '').strip()
        session = TrainingSession(
            user_id=current_user.id, date=session_date,
            duration_minutes=duration, notes=notes or None)
        db.session.add(session)
        db.session.commit()
        recalculate_xp(current_user.id)
        db.session.commit()
        flash('Sesión creada. Agrega tus ejercicios.')
        return redirect(url_for('ver_sesion', sesion_id=session.id))
    return render_template('registrar.html', today=date.today())


# ─── Ver sesión + agregar ejercicios ────────────────────────────────

@app.route('/sesion/<int:sesion_id>')
@login_required
def ver_sesion(sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != current_user.id:
        flash('Sesión no encontrada')
        return redirect(url_for('dashboard'))
    templates = ExerciseTemplate.query.order_by(
        ExerciseTemplate.category, ExerciseTemplate.name).all()
    custom = CustomExercise.query.filter_by(user_id=current_user.id).all()
    return render_template('sesion.html', session=session,
                           templates=templates, custom=custom)


@app.route('/sesion/<int:sesion_id>/agregar', methods=['POST'])
@login_required
def agregar_ejercicio(sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != current_user.id:
        flash('Sesión no encontrada')
        return redirect(url_for('dashboard'))
    name = request.form.get('name', '').strip()
    category = request.form.get('category', 'free')
    if not name:
        flash('Escribe el nombre del ejercicio')
        return redirect(url_for('ver_sesion', sesion_id=sesion_id))
    sets = request.form.get('sets', type=int)
    reps = request.form.get('reps', type=int)
    weight = request.form.get('weight_kg', type=float)
    distance = request.form.get('distance_km', type=float)
    time_min = request.form.get('time_minutes', type=float)
    log = ExerciseLog(
        session_id=sesion_id, name=name, category=category,
        sets=sets, reps=reps, weight_kg=weight,
        distance_km=distance, time_minutes=time_min)
    db.session.add(log)
    db.session.commit()
    recalculate_xp(current_user.id)
    db.session.commit()
    flash(f'{name} agregado')
    return redirect(url_for('ver_sesion', sesion_id=sesion_id))


@app.route('/ejercicio/<int:ej_id>/eliminar')
@login_required
def eliminar_ejercicio(ej_id):
    ej = db.session.get(ExerciseLog, ej_id)
    if not ej:
        flash('Ejercicio no encontrado')
        return redirect(url_for('dashboard'))
    session = db.session.get(TrainingSession, ej.session_id)
    if not session or session.user_id != current_user.id:
        flash('No autorizado')
        return redirect(url_for('dashboard'))
    sesion_id = session.id
    uid = session.user_id
    db.session.delete(ej)
    db.session.commit()
    recalculate_xp(uid)
    db.session.commit()
    return redirect(url_for('ver_sesion', sesion_id=sesion_id))


@app.route('/sesion/<int:sesion_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_sesion(sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != current_user.id:
        flash('Sesión no encontrada')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            session.date = datetime.strptime(
                request.form['date'], '%Y-%m-%d').date()
        except (ValueError, KeyError):
            pass
        session.duration_minutes = request.form.get(
            'duration_minutes', type=int)
        session.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('Sesión actualizada')
        return redirect(url_for('ver_sesion', sesion_id=sesion_id))
    return render_template('editar_sesion.html', session=session)


@app.route('/sesion/<int:sesion_id>/eliminar')
@login_required
def eliminar_sesion(sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != current_user.id:
        flash('Sesión no encontrada')
        return redirect(url_for('dashboard'))
    for ej in session.exercises:
        db.session.delete(ej)
    for c in session.comments:
        db.session.delete(c)
    db.session.delete(session)
    db.session.commit()
    recalculate_xp(current_user.id)
    db.session.commit()
    flash('Sesión eliminada')
    return redirect(url_for('dashboard'))


# ─── Comentarios ────────────────────────────────────────────────────

@app.route('/sesion/<int:sesion_id>/comentar', methods=['POST'])
@login_required
def comentar(sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session:
        flash('Sesión no encontrada')
        return redirect(url_for('dashboard'))
    text = request.form.get('text', '').strip()
    if not text:
        flash('Escribe un comentario')
    else:
        c = Comment(session_id=sesion_id, user_id=current_user.id, text=text)
        db.session.add(c)
        db.session.commit()
        flash('Comentario agregado')
    return redirect(url_for('ver_sesion_usuario',
        user_id=session.user_id, sesion_id=sesion_id))


# ─── Ejercicios personalizados ──────────────────────────────────────

@app.route('/ejercicios-personalizados', methods=['GET', 'POST'])
@login_required
def ejercicios_personalizados():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category = request.form.get('category', 'free')
        if not name:
            flash('Escribe el nombre')
        elif CustomExercise.query.filter_by(user_id=current_user.id, name=name).first():
            flash('Ya tienes ese ejercicio')
        else:
            db.session.add(CustomExercise(
                user_id=current_user.id, name=name, category=category))
            db.session.commit()
            flash(f'{name} creado')
        return redirect(url_for('ejercicios_personalizados'))
    exercises = CustomExercise.query.filter_by(user_id=current_user.id).all()
    return render_template('ejercicios_personalizados.html', exercises=exercises)


@app.route('/ejercicio-personalizado/<int:ex_id>/eliminar')
@login_required
def eliminar_ejercicio_personalizado(ex_id):
    ex = db.session.get(CustomExercise, ex_id)
    if not ex or ex.user_id != current_user.id:
        flash('Ejercicio no encontrado')
        return redirect(url_for('ejercicios_personalizados'))
    db.session.delete(ex)
    db.session.commit()
    return redirect(url_for('ejercicios_personalizados'))


# ─── Peso corporal ──────────────────────────────────────────────────

@app.route('/peso', methods=['GET', 'POST'])
@login_required
def peso():
    if request.method == 'POST':
        try:
            w = float(request.form['weight'])
            d = request.form.get('date') or str(date.today())
            d = datetime.strptime(d, '%Y-%m-%d').date()
        except (ValueError, KeyError):
            flash('Datos inválidos')
            return redirect(url_for('peso'))
        if w <= 0 or w > 300:
            flash('Peso inválido')
            return redirect(url_for('peso'))
        existing = BodyWeight.query.filter_by(
            user_id=current_user.id, date=d).first()
        if existing:
            existing.weight_kg = w
        else:
            db.session.add(BodyWeight(
                user_id=current_user.id, date=d, weight_kg=w))
        db.session.commit()
        flash('Peso registrado')
        return redirect(url_for('peso'))
    records = BodyWeight.query.filter_by(user_id=current_user.id)\
        .order_by(BodyWeight.date.desc()).all()
    return render_template('peso.html', records=records, today=date.today())


# ─── Historial mensual ──────────────────────────────────────────────

@app.route('/historial')
@login_required
def historial():
    today = date.today()
    return redirect(url_for('historial_mes', year=today.year, month=today.month))


@app.route('/historial/<int:year>/<int:month>')
@login_required
def historial_mes(year, month):
    if month < 1 or month > 12:
        today = date.today()
        return redirect(url_for('historial_mes', year=today.year, month=today.month))
    first, last = month_range(year, month)
    sessions = TrainingSession.query.filter(
        TrainingSession.user_id == current_user.id,
        TrainingSession.date >= first,
        TrainingSession.date <= last
    ).order_by(TrainingSession.date.desc()).all()
    total_volume = sum(
        e.volume() for s in sessions for e in s.exercises)
    total_duration = sum(s.duration_minutes or 0 for s in sessions)
    month_name = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                  'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
    prev_m = month - 1 or 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    return render_template('historial.html',
        sessions=sessions, year=year, month=month,
        month_name=month_name[month-1],
        total_sessions=len(sessions),
        total_volume=round(total_volume),
        total_duration=total_duration,
        prev_m=prev_m, prev_y=prev_y,
        next_m=next_m, next_y=next_y,
        today=date.today())


# ─── Exportar CSV ───────────────────────────────────────────────────

@app.route('/exportar')
@login_required
def exportar():
    sessions = TrainingSession.query.filter_by(user_id=current_user.id)\
        .order_by(TrainingSession.date).all()
    output = StringIO()
    w = csv.writer(output)
    w.writerow(['Fecha','Duración(min)','Notas','Ejercicio','Categoría',
                'Series','Reps','Peso(kg)','Distancia(km)','Tiempo(min)'])
    for s in sessions:
        if s.exercises:
            for e in s.exercises:
                w.writerow([s.date, s.duration_minutes, s.notes,
                    e.name, e.category, e.sets, e.reps, e.weight_kg,
                    e.distance_km, e.time_minutes])
        else:
            w.writerow([s.date, s.duration_minutes, s.notes,
                        '','','','','','',''])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition':
                 'attachment;filename=entrenamientos.csv'})


# ─── Progreso grupal ────────────────────────────────────────────────

@app.route('/progreso')
@login_required
def progreso():
    try:
        results, monday, sunday, multa = all_users_week_progress()
        week_days = [(monday + timedelta(days=i)) for i in range(7)]
        user_ranks = {}
        for r in results:
            uid = r['user'].id
            pxp = PlayerXP.query.get(uid)
            rank_id = pxp.rank if pxp else 'bronze'
            cfg = get_rank_config(rank_id)
            cfg['level'] = pxp.level if pxp else 0
            user_ranks[uid] = cfg
        return render_template('progreso.html',
            results=results, monday=monday, sunday=sunday,
            multa_por_dia=multa, week_days=week_days,
            user_ranks=user_ranks)
    except Exception as e:
        print(f'Error en progreso: {e}', file=sys.stderr)
        flash('Error al cargar el progreso. Intenta de nuevo.')
        return redirect(url_for('dashboard'))


# ─── Calendario ─────────────────────────────────────────────────────

VIEW_NAMES = {'day': 'Día', 'week': 'Semana', 'month': 'Mes', 'year': 'Año'}

@app.route('/calendario')
@login_required
def calendario():
    return redirect(url_for('calendario_view', view='month',
                            year=date.today().year, month=date.today().month, day=date.today().day))


@app.route('/calendario/<view>/<int:year>/<int:month>/<int:day>')
@login_required
def calendario_view(view, year, month, day):
    if view not in ('day', 'week', 'month', 'year'):
        return redirect(url_for('calendario'))
    try:
        current_date = date(year, month, day)
    except ValueError:
        current_date = date.today()

    sessions = TrainingSession.query.filter_by(user_id=current_user.id)\
        .order_by(TrainingSession.date).all()
    session_dates = {s.date for s in sessions}

    pxp = PlayerXP.query.get(current_user.id)
    rank_info = get_rank_config(pxp.rank if pxp else 'bronze')
    xp_data = {'xp': pxp.xp if pxp else 0, 'level': pxp.level if pxp else 0,
               'rank': rank_info}

    ctx = {
        'view': view, 'current_date': current_date,
        'sessions': sessions, 'session_dates': session_dates,
        'xp_data': xp_data, 'VIEW_NAMES': VIEW_NAMES,
    }

    if view == 'day':
        day_sessions = [s for s in sessions if s.date == current_date]
        ctx['day_sessions'] = day_sessions
        ctx['prev'] = current_date - timedelta(days=1)
        ctx['next'] = current_date + timedelta(days=1)
        return render_template('calendario.html', **ctx)

    elif view == 'week':
        monday = current_date - timedelta(days=current_date.weekday())
        sunday = monday + timedelta(days=6)
        week_days = [monday + timedelta(days=i) for i in range(7)]
        ctx['week_days'] = week_days
        ctx['monday'] = monday
        ctx['sunday'] = sunday
        ctx['prev'] = monday - timedelta(days=7)
        ctx['next'] = monday + timedelta(days=7)
        return render_template('calendario.html', **ctx)

    elif view == 'month':
        first, last = month_range(year, month)
        month_days = []
        d = first
        while d <= last:
            month_days.append(d)
            d += timedelta(days=1)
        start_padding = first.weekday()
        ctx['month_days'] = month_days
        ctx['start_padding'] = start_padding
        ctx['month'] = month
        ctx['year'] = year
        prev_m = month - 1 or 12
        ctx['prev'] = date(year - 1 if month == 1 else year, prev_m, 1)
        next_m = month + 1 if month < 12 else 1
        ctx['next'] = date(year + 1 if month == 12 else year, next_m, 1)
        ctx['month_name'] = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                             'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][month-1]
        return render_template('calendario.html', **ctx)

    elif view == 'year':
        months = []
        for m in range(1, 13):
            first, last = month_range(year, m)
            month_sessions = [s for s in sessions if first <= s.date <= last]
            months.append({
                'num': m,
                'name': ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                         'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][m-1],
                'count': len(month_sessions),
                'days': len([d for d in range(1, (last - first).days + 2) if date(year, m, d) in session_dates]),
            })
        ctx['months'] = months
        ctx['year'] = year
        ctx['prev'] = date(year - 1, 1, 1)
        ctx['next'] = date(year + 1, 1, 1)
        ctx['year_sessions'] = len([s for s in sessions if s.date.year == year])
        return render_template('calendario.html', **ctx)


# ─── Stats / Perfil ────────────────────────────────────────────────

@app.route('/stats')
@login_required
def stats_self():
    return redirect(url_for('stats', user_id=current_user.id))


@app.route('/stats/<int:user_id>')
@login_required
def stats(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Usuario no encontrado')
        return redirect(url_for('progreso'))

    all_sessions = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date).all()

    total_sessions = len(all_sessions)
    total_exercises = sum(len(s.exercises) for s in all_sessions)
    total_volume = sum(e.volume() for s in all_sessions for e in s.exercises)
    total_duration = sum(s.duration_minutes or 0 for s in all_sessions)
    total_weight_logs = BodyWeight.query.filter_by(user_id=user_id).count()

    pxp = PlayerXP.query.get(user_id)
    rank_info = get_rank_config(pxp.rank if pxp else 'bronze')
    xp_current = pxp.xp if pxp else 0
    xp_level = pxp.level if pxp else 0

    current_streak = get_streak(user_id)

    best_streak = 0
    streak_count = 0
    prev_date = None
    for s in all_sessions:
        if prev_date:
            diff = (s.date - prev_date).days
            if diff == 1:
                streak_count += 1
            elif diff > 1:
                streak_count = 0
        else:
            streak_count = 0
        best_streak = max(best_streak, streak_count)
        prev_date = s.date
    best_streak += 1

    this_monday, this_sunday = week_range()
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_sunday - timedelta(days=7)

    this_week_sessions = get_sessions_in_range(user_id, this_monday, this_sunday)
    this_week_days = len(this_week_sessions)
    this_week_vol = sum(e.volume() for s in this_week_sessions for e in s.exercises)

    last_week_sessions = get_sessions_in_range(user_id, last_monday, last_sunday)
    last_week_days = len(last_week_sessions)
    last_week_vol = sum(e.volume() for s in last_week_sessions for e in s.exercises)

    months_data = []
    for i in range(5, -1, -1):
        y = this_monday.year
        m = this_monday.month - i
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        first, last = month_range(y, m)
        count = sum(1 for s in all_sessions if first <= s.date <= last)
        months_data.append({
            'name': ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                     'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][m-1][:3],
            'count': count,
            'max': 31,
        })
    max_count = max(m['count'] for m in months_data) or 1

    recent_sessions = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date.desc()).limit(10).all()

    body_weights = BodyWeight.query.filter_by(user_id=user_id)\
        .order_by(BodyWeight.date.desc()).all()

    return render_template('stats.html',
        target_user=user, rank_info=rank_info,
        xp=xp_current, level=xp_level,
        total_sessions=total_sessions,
        total_exercises=total_exercises,
        total_volume=round(total_volume),
        total_duration=total_duration,
        total_weight_logs=total_weight_logs,
        current_streak=current_streak,
        best_streak=best_streak,
        this_week_days=this_week_days,
        this_week_vol=round(this_week_vol),
        last_week_days=last_week_days,
        last_week_vol=round(last_week_vol),
        months_data=months_data,
        max_count=max_count,
        recent_sessions=recent_sessions,
        body_weights=body_weights,
        is_owner=(current_user.id == user_id))


# ─── Admin ──────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin():
    users = User.query.all()
    multa = get_multa()
    xp_ok = all(PlayerXP.query.get(u.id) for u in users) if users else True
    return render_template('admin.html', users=users, multa_por_dia=multa, xp_ok=xp_ok)


@app.route('/admin/multa', methods=['POST'])
@login_required
@admin_required
def admin_multa():
    try:
        val = int(request.form['multa'])
        if val < 0:
            raise ValueError
    except (ValueError, KeyError):
        flash('Valor inválido')
        return redirect(url_for('admin'))
    s = Setting.query.get('multa_por_dia')
    if s:
        s.value = str(val)
    else:
        db.session.add(Setting(key='multa_por_dia', value=str(val)))
    db.session.commit()
    flash(f'Multa actualizada a ${val}')
    return redirect(url_for('admin'))


@app.route('/admin/recalcular')
@login_required
@admin_required
def admin_recalcular():
    recalculate_all_xp()
    users = User.query.all()
    results = []
    for u in users:
        pxp = PlayerXP.query.get(u.id)
        results.append(f'{u.username}: Nv.{pxp.level if pxp else 0} | {pxp.xp if pxp else 0} XP | {pxp.rank if pxp else "bronze"}')
    flash('XP y rangos recalculados para todos los usuarios')
    return redirect(url_for('admin'))


@app.route('/admin/usuario/<int:user_id>')
@login_required
@admin_required
def admin_usuario(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Usuario no encontrado')
        return redirect(url_for('admin'))
    sessions = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date.desc()).all()
    total_exercises = sum(len(s.exercises) for s in sessions)
    return render_template('admin_usuario.html',
        user=user, sessions=sessions, total_exercises=total_exercises)


@app.route('/admin/usuario/<int:user_id>/eliminar')
@login_required
@admin_required
def admin_eliminar_usuario(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Usuario no encontrado')
        return redirect(url_for('admin'))
    if user.is_admin:
        flash('No puedes eliminar a otro admin')
        return redirect(url_for('admin'))
    for c in Comment.query.filter_by(user_id=user.id).all():
        db.session.delete(c)
    for s in user.sessions:
        for e in s.exercises:
            db.session.delete(e)
        for c_ in s.comments:
            db.session.delete(c_)
        db.session.delete(s)
    for w in user.body_weights:
        db.session.delete(w)
    for ce in user.custom_exercises:
        db.session.delete(ce)
    pxp = PlayerXP.query.get(user.id)
    if pxp:
        db.session.delete(pxp)
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {user.username} eliminado')
    return redirect(url_for('admin'))


# ─── Ver sesión de otro usuario ─────────────────────────────────────

@app.route('/usuario/<int:user_id>/sesion/<int:sesion_id>')
@login_required
def ver_sesion_usuario(user_id, sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != user_id:
        flash('Sesión no encontrada')
        return redirect(url_for('progreso'))
    return render_template('ver_sesion_usuario.html', session=session)


_initialized = False

@app.before_request
def init_app():
    global _initialized
    if not _initialized:
        _initialized = True
        try:
            migrate_db()
        except Exception as e:
            print(f'Init error (migrate): {e}', file=sys.stderr)
        try:
            seed_exercises()
        except Exception as e:
            print(f'Init error (seed): {e}', file=sys.stderr)
        for user in User.query.all():
            pxp = PlayerXP.query.get(user.id)
            if pxp:
                continue
            try:
                recalculate_xp(user.id)
            except Exception as e:
                print(f'Init XP error for {user.username}: {e}', file=sys.stderr)
        try:
            db.session.commit()
            for user in User.query.all():
                pxp = PlayerXP.query.get(user.id)
                if pxp:
                    pxp.rank = calculate_rank(user.id, pxp.level)
            db.session.commit()
        except Exception as e:
            print(f'Init error (rank): {e}', file=sys.stderr)

@app.route('/usuario/<int:user_id>/ultima-sesion')
@login_required
def ver_ultima_sesion_usuario(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Usuario no encontrado')
        return redirect(url_for('progreso'))
    session = TrainingSession.query.filter_by(user_id=user_id)\
        .order_by(TrainingSession.date.desc()).first()
    if not session:
        flash(f'{user.username} aún no tiene entrenamientos')
        return redirect(url_for('progreso'))
    return redirect(url_for('ver_sesion_usuario',
        user_id=user_id, sesion_id=session.id))


if __name__ == '__main__':
    with app.app_context():
        migrate_db()
        seed_exercises()
    app.run(host='0.0.0.0', port=5000, debug=True)
