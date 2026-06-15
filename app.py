import os
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cambia-esta-clave-123')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///entrenamiento.db'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

MULTA_POR_DIA = int(os.environ.get('MULTA_POR_DIA', 50))

# ─── Modelos ────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    sessions = db.relationship('TrainingSession', backref='user', lazy=True,
                                order_by='TrainingSession.date.desc()')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)


class TrainingSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    duration_minutes = db.Column(db.Integer)
    notes = db.Column(db.Text)
    exercises = db.relationship('ExerciseLog', backref='session', lazy=True,
                                 order_by='ExerciseLog.id')

    __table_args__ = (db.UniqueConstraint('user_id', 'date'),)


class ExerciseLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('training_session.id'),
                           nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(20))  # gym, cardio, free
    sets = db.Column(db.Integer)
    reps = db.Column(db.Integer)
    weight_kg = db.Column(db.Float)
    distance_km = db.Column(db.Float)
    time_minutes = db.Column(db.Float)


class ExerciseTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(20), nullable=False)  # gym, cardio, free


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


def get_sessions_in_range(user_id, start, end):
    return TrainingSession.query.filter(
        TrainingSession.user_id == user_id,
        TrainingSession.date >= start,
        TrainingSession.date <= end
    ).all()


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
        total_exercises = sum(len(s.exercises) for s in sessions)
        results.append({
            'user': user,
            'trained_days': trained_days,
            'days_rested': 7 - trained_days,
            'penalty': penalty,
            'trained_dates': trained_dates,
            'cumple': trained_days >= 5,
            'total_exercises': total_exercises,
            'sessions': sessions,
        })
    return results, monday, sunday, multa


def seed_exercises():
    if ExerciseTemplate.query.first():
        return
    for name, cat in PREDEFINED:
        db.session.add(ExerciseTemplate(name=name, category=cat))
    db.session.commit()


def get_multa():
    s = Setting.query.get('multa_por_dia')
    return int(s.value) if s else int(os.environ.get('MULTA_POR_DIA', 50))


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
        if User.query.count() == 0:
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
    return render_template('dashboard.html',
        monday=monday, sunday=sunday, today=today,
        trained_count=trained_count, trained_dates=trained_dates,
        today_session=today_session, penalty=penalty,
        cumple=trained_count >= 5, week_days=week_days,
        day_names=day_names)


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
        # check existing
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
    return render_template('sesion.html', session=session,
                           templates=templates)


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
    db.session.delete(session)
    db.session.commit()
    flash('Sesión eliminada')
    return redirect(url_for('dashboard'))


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
    for s in user.sessions:
        for e in s.exercises:
            db.session.delete(e)
        db.session.delete(s)
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {user.username} eliminado')
    return redirect(url_for('admin'))


# ─── Ver sesión de otro usuario (solo lectura) ──────────────────────

@app.route('/usuario/<int:user_id>/sesion/<int:sesion_id>')
@login_required
def ver_sesion_usuario(user_id, sesion_id):
    session = db.session.get(TrainingSession, sesion_id)
    if not session or session.user_id != user_id:
        flash('Sesión no encontrada')
        return redirect(url_for('progreso'))
    if session.user_id != current_user.id and not current_user.is_admin:
        flash('No autorizado')
        return redirect(url_for('progreso'))
    return render_template('ver_sesion_usuario.html', session=session)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_exercises()
    app.run(host='0.0.0.0', port=5000, debug=True)
