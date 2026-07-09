from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from app.models import User


class LoginForm(FlaskForm):
    """Login form with CSRF protection."""
    username = StringField('用户名', validators=[
        DataRequired(message='请输入用户名'),
        Length(min=2, max=80, message='用户名长度须在2-80字符之间')
    ])
    password = PasswordField('密码', validators=[
        DataRequired(message='请输入密码')
    ])
    submit = SubmitField('登录')


class RegistrationForm(FlaskForm):
    """Registration form with CSRF protection."""
    username = StringField('用户名', validators=[
        DataRequired(message='请输入用户名'),
        Length(min=2, max=80, message='用户名长度须在2-80字符之间')
    ])
    email = StringField('邮箱', validators=[
        DataRequired(message='请输入邮箱'),
        Email(message='请输入有效的邮箱地址'),
        Length(max=120)
    ])
    password = PasswordField('密码', validators=[
        DataRequired(message='请输入密码'),
        Length(min=6, message='密码长度至少6位')
    ])
    password2 = PasswordField('确认密码', validators=[
        DataRequired(message='请再次输入密码'),
        EqualTo('password', message='两次密码输入不一致')
    ])
    submit = SubmitField('注册')

    def validate_username(self, username):
        """Check if username is already taken."""
        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError('该用户名已被注册，请使用其他用户名。')

    def validate_email(self, email):
        """Check if email is already taken."""
        user = User.query.filter_by(email=email.data).first()
        if user is not None:
            raise ValidationError('该邮箱已被注册，请使用其他邮箱。')
