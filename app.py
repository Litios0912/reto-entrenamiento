import os
import csv
import sys
from io import StringIO
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, Response
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

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
    return render_template('dashboard.html',
        monday=monday, sunday=sunday, today=today,
        trained_count=trained_count, trained_dates=trained_dates,
        today_session=today_session, penalty=penalty,
        cumple=trained_count >= 5, week_days=week_days,
        day_names=day_names, streak=streak,
        weekly_volume=weekly_volume, last_weight=last_weight)


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
    db.session.delete(ej)
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
    results, monday, sunday, multa = all_users_week_progress()
    week_days = [(monday + timedelta(days=i)) for i in range(7)]
    return render_template('progreso.html',
        results=results, monday=monday, sunday=sunday,
        multa_por_dia=multa, week_days=week_days)


# ─── Admin ──────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin():
    users = User.query.all()
    multa = get_multa()
    return render_template('admin.html', users=users, multa_por_dia=multa)


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
            seed_exercises()
        except Exception as e:
            print(f'Init error: {e}', file=sys.stderr)

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
