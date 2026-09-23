"""
Bullet Frenzy - a small 3D arena shooter built with PyOpenGL and GLUT.

Enemies spawn around a walled, checkered arena and close in on you.
Shoot them before they reach you, and don't waste bullets: the game ends
when you run out of lives or miss too many shots.

Run with:  python bullet_frenzy.py
"""

import math
import random
import time
from dataclasses import dataclass

from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

WINDOW_TITLE = b"Bullet Frenzy"
WINDOW_WIDTH, WINDOW_HEIGHT = 1000, 800
FOV_Y = 60

GRID_LENGTH = 600            # the arena spans -GRID_LENGTH..GRID_LENGTH on X and Y
TILE_SIZE = 100
WALL_HEIGHT = 100

START_LIVES = 5
MAX_MISSED_BULLETS = 10
ENEMY_COUNT = 5
ENEMY_SPAWN_DISTANCE = 300   # enemies never spawn closer than this to the player
HIT_DISTANCE = 40            # collision range for bullet/enemy and enemy/player
POINTS_PER_KILL = 10

PLAYER_STEP = 30.0           # units moved per W/S press
PLAYER_TURN = 15.0           # degrees turned per A/D press
PLAYER_RADIUS = 30           # keeps the player model inside the walls
MUZZLE_DISTANCE = 40         # bullets spawn this far in front of the player

CAM_RADIUS = 800.0
CAM_HEIGHT_MIN, CAM_HEIGHT_MAX = 20.0, 1500.0

# Continuous motion is time-based, so the game plays the same at any frame rate.
ENEMY_SPEED = 14.0           # units per second
BULLET_SPEED = 4000.0        # units per second
PULSE_SPEED = 12.0           # radians per second (enemy "breathing" animation)
CHEAT_SPIN_SPEED = 400.0     # degrees per second
CHEAT_FIRE_COOLDOWN = 0.06   # seconds between automatic shots
CHEAT_AIM_RADIUS = 30       # cheat mode fires when a shot would pass this close to an enemy (< HIT_DISTANCE)

SIM_STEP = 1.0 / 240.0       # fixed physics step, small enough that bullets can't skip past an enemy
MAX_FRAME_TIME = 0.25        # ignore long stalls (e.g. dragging the window) instead of fast-forwarding

CONTROLS_HINT = ("W/S move   A/D turn   Left click shoot   Right click camera   "
                 "Arrows orbit   C cheat   V cheat vision   R restart   Esc quit")


def clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Game state and rules
# ---------------------------------------------------------------------------

@dataclass
class Enemy:
    x: float
    y: float


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float


class Game:
    """Holds all game state and rules. Rendering lives in the draw_* functions below."""

    def __init__(self):
        # Camera settings are kept across restarts.
        self.cam_angle = -90.0
        self.cam_height = 300.0
        self.first_person = False
        self.reset()

    def reset(self):
        self.player_x = 0.0
        self.player_y = 0.0
        self.player_angle = 90.0     # gun heading in degrees; 90 means facing +Y
        self.view_angle = 90.0       # first-person heading while cheat mode spins the gun
        self.lives = START_LIVES
        self.score = 0
        self.missed_bullets = 0
        self.game_over = False
        self.cheat_mode = False
        self.cheat_vision = False
        self.cheat_cooldown = 0.0
        self.pulse_time = 0.0
        self.bullets = []
        self.enemies = []
        for _ in range(ENEMY_COUNT):
            self.spawn_enemy()

    def spawn_enemy(self):
        """Spawn an enemy at a random spot that isn't too close to the player."""
        limit = GRID_LENGTH - 50
        while True:
            x = random.uniform(-limit, limit)
            y = random.uniform(-limit, limit)
            if math.hypot(x - self.player_x, y - self.player_y) > ENEMY_SPAWN_DISTANCE:
                self.enemies.append(Enemy(x, y))
                return

    # --- Player actions ----------------------------------------------------

    def move_player(self, direction):
        """Step forward (direction=1) or backward (direction=-1) along the gun heading."""
        if self.game_over:
            return
        rad = math.radians(self.player_angle)
        limit = GRID_LENGTH - PLAYER_RADIUS
        self.player_x = clamp(self.player_x + direction * math.cos(rad) * PLAYER_STEP, -limit, limit)
        self.player_y = clamp(self.player_y + direction * math.sin(rad) * PLAYER_STEP, -limit, limit)

    def turn_player(self, degrees):
        if self.game_over:
            return
        self.player_angle = (self.player_angle + degrees) % 360
        self.view_angle = (self.view_angle + degrees) % 360

    def fire_bullet(self):
        if self.game_over:
            return
        rad = math.radians(self.player_angle)
        dir_x, dir_y = math.cos(rad), math.sin(rad)
        self.bullets.append(Bullet(self.player_x + dir_x * MUZZLE_DISTANCE,
                                   self.player_y + dir_y * MUZZLE_DISTANCE,
                                   dir_x * BULLET_SPEED, dir_y * BULLET_SPEED))
        print("Player Bullet Fired!")

    def toggle_cheat_mode(self):
        if self.game_over:
            return
        self.cheat_mode = not self.cheat_mode
        self.view_angle = self.player_angle
        print(f"Cheat Mode: {'ON' if self.cheat_mode else 'OFF'}")

    def toggle_cheat_vision(self):
        if self.game_over:
            return
        self.cheat_vision = not self.cheat_vision
        self.view_angle = self.player_angle
        print(f"Cheat Vision: {'ON' if self.cheat_vision else 'OFF'}")

    def first_person_angle(self):
        """In cheat mode the first-person camera only follows the spinning gun if cheat vision is on."""
        if self.cheat_mode and not self.cheat_vision:
            return self.view_angle
        return self.player_angle

    # --- Simulation --------------------------------------------------------

    def update(self, dt):
        """Advance the game by dt seconds."""
        if self.game_over:
            return
        self.pulse_time += PULSE_SPEED * dt
        if self.cheat_mode:
            self._update_cheat(dt)
        self._update_bullets(dt)
        if not self.game_over:
            self._update_enemies(dt)

    def _update_cheat(self, dt):
        """Spin the gun and auto-fire whenever a shot is guaranteed to hit an enemy."""
        self.player_angle = (self.player_angle + CHEAT_SPIN_SPEED * dt) % 360
        self.cheat_cooldown = max(0.0, self.cheat_cooldown - dt)
        if self.cheat_cooldown > 0:
            return

        rad = math.radians(self.player_angle)
        dir_x, dir_y = math.cos(rad), math.sin(rad)
        for en in self.enemies:
            dx, dy = en.x - self.player_x, en.y - self.player_y
            ahead = dir_x * dx + dir_y * dy          # distance along the gun direction
            off_line = abs(dir_x * dy - dir_y * dx)  # how far the enemy is from the line of fire
            if ahead > 0 and off_line < CHEAT_AIM_RADIUS:
                self.fire_bullet()
                self.cheat_cooldown = CHEAT_FIRE_COOLDOWN
                return

    def _update_bullets(self, dt):
        for b in self.bullets[:]:
            b.x += b.vx * dt
            b.y += b.vy * dt

            if abs(b.x) > GRID_LENGTH or abs(b.y) > GRID_LENGTH:
                self.bullets.remove(b)
                self.missed_bullets += 1
                print(f"Bullet missed: {self.missed_bullets}")
                if self.missed_bullets >= MAX_MISSED_BULLETS:
                    self._end_game("Too many missed bullets!")
                    return
                continue

            target = next((en for en in self.enemies
                           if math.hypot(b.x - en.x, b.y - en.y) < HIT_DISTANCE), None)
            if target:
                self.bullets.remove(b)
                self.enemies.remove(target)
                self.score += POINTS_PER_KILL
                self.spawn_enemy()

    def _update_enemies(self, dt):
        """Move every enemy toward the player; one that touches the player costs a life."""
        for en in self.enemies[:]:
            dx, dy = self.player_x - en.x, self.player_y - en.y
            dist = math.hypot(dx, dy)

            if dist < HIT_DISTANCE:
                self.enemies.remove(en)
                self.spawn_enemy()
                self.lives -= 1
                print(f"Remaining Player Life: {self.lives}")
                if self.lives <= 0:
                    self._end_game("You ran out of lives!")
                    return
            else:
                en.x += dx / dist * ENEMY_SPEED * dt
                en.y += dy / dist * ENEMY_SPEED * dt

    def _end_game(self, reason):
        self.game_over = True
        print(f"GAME OVER - {reason}")


game = Game()
quadric = None               # shared GLU quadric, created once the GL context exists
window_width, window_height = WINDOW_WIDTH, WINDOW_HEIGHT
last_frame_time = None
time_accumulator = 0.0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def begin_2d():
    """Switch to a pixel-based projection (origin at the bottom-left) for drawing the HUD."""
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, window_width, 0, window_height)
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()


def end_2d():
    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)


def draw_text(x, y, text, font=GLUT_BITMAP_HELVETICA_18, color=(1, 1, 1)):
    """Draw text at window pixel (x, y). Call between begin_2d() and end_2d()."""
    # A dark drop shadow keeps the text readable over the white floor tiles.
    for (dx, dy), rgb in (((1, -1), (0, 0, 0)), ((0, 0), color)):
        glColor3f(*rgb)
        glRasterPos2f(x + dx, y + dy)
        for ch in text:
            glutBitmapCharacter(font, ord(ch))


def draw_centered_text(y, text, font=GLUT_BITMAP_HELVETICA_18, color=(1, 1, 1)):
    width = sum(glutBitmapWidth(font, ord(ch)) for ch in text)
    draw_text((window_width - width) / 2, y, text, font, color)


def draw_environment():
    # Checkered floor
    glBegin(GL_QUADS)
    for x in range(-GRID_LENGTH, GRID_LENGTH, TILE_SIZE):
        for y in range(-GRID_LENGTH, GRID_LENGTH, TILE_SIZE):
            if (x // TILE_SIZE + y // TILE_SIZE) % 2 == 0:
                glColor3f(1.0, 1.0, 1.0)
            else:
                glColor3f(0.7, 0.5, 0.95)
            glVertex3f(x, y, 0)
            glVertex3f(x + TILE_SIZE, y, 0)
            glVertex3f(x + TILE_SIZE, y + TILE_SIZE, 0)
            glVertex3f(x, y + TILE_SIZE, 0)
    glEnd()

    # Walls on three sides; the side facing the default camera stays open.
    g, h = GRID_LENGTH, WALL_HEIGHT
    glBegin(GL_QUADS)
    glColor3f(0.0, 0.0, 1.0)     # left
    glVertex3f(-g, -g, 0)
    glVertex3f(-g, g, 0)
    glVertex3f(-g, g, h)
    glVertex3f(-g, -g, h)

    glColor3f(0.0, 1.0, 1.0)     # back
    glVertex3f(-g, g, 0)
    glVertex3f(g, g, 0)
    glVertex3f(g, g, h)
    glVertex3f(-g, g, h)

    glColor3f(0.0, 1.0, 0.0)     # right
    glVertex3f(g, g, 0)
    glVertex3f(g, -g, 0)
    glVertex3f(g, -g, h)
    glVertex3f(g, g, h)
    glEnd()


def draw_player():
    glPushMatrix()
    glTranslatef(game.player_x, game.player_y, 0)
    glRotatef(game.player_angle - 90, 0, 0, 1)   # model faces +Y, so rotate it onto the gun heading
    if game.game_over:
        # Topple over backwards, lifted so the body rests on the floor instead of sinking into it.
        glTranslatef(0, 0, 20)
        glRotatef(90, 1, 0, 0)

    # Body
    glColor3f(0.3, 0.5, 0.2)
    glPushMatrix()
    glTranslatef(0, 0, 50)
    glScalef(0.8, 0.4, 1.2)
    glutSolidCube(60)
    glPopMatrix()

    # Head
    glColor3f(0.1, 0.1, 0.1)
    glPushMatrix()
    glTranslatef(0, 0, 100)
    gluSphere(quadric, 20, 20, 20)
    glPopMatrix()

    # Gun, pointing forward
    glColor3f(0.8, 0.8, 0.8)
    glPushMatrix()
    glTranslatef(15, 35, 85)
    glRotatef(-90, 1, 0, 0)
    gluCylinder(quadric, 5, 2, 60, 10, 10)
    glPopMatrix()

    # Legs, from the hips down to the floor
    glColor3f(0.1, 0.1, 0.8)
    for side in (-15, 15):
        glPushMatrix()
        glTranslatef(side, 0, 30)
        glRotatef(180, 1, 0, 0)
        gluCylinder(quadric, 8, 6, 30, 10, 10)
        glPopMatrix()

    glPopMatrix()


def draw_enemies():
    scale = 1.0 + 0.2 * math.sin(game.pulse_time)
    for en in game.enemies:
        glPushMatrix()
        glTranslatef(en.x, en.y, 0)
        glScalef(scale, scale, scale)

        glColor3f(1.0, 0.0, 0.0)     # body
        glPushMatrix()
        glTranslatef(0, 0, 20)
        gluSphere(quadric, 20, 20, 20)
        glPopMatrix()

        glColor3f(0.0, 0.0, 0.0)     # head
        glPushMatrix()
        glTranslatef(0, 0, 50)
        gluSphere(quadric, 15, 20, 20)
        glPopMatrix()

        glPopMatrix()


def draw_bullets():
    glColor3f(1.0, 1.0, 0.0)
    for b in game.bullets:
        glPushMatrix()
        glTranslatef(b.x, b.y, 60)
        glutSolidCube(10)
        glPopMatrix()


def draw_hud():
    glDisable(GL_DEPTH_TEST)
    begin_2d()

    top = window_height - 30
    draw_text(10, top, f"Player Life Remaining: {game.lives}")
    draw_text(10, top - 30, f"Game Score: {game.score}")
    draw_text(10, top - 60, f"Player Bullet Missed: {game.missed_bullets}/{MAX_MISSED_BULLETS}")
    if game.cheat_mode:
        vision = "ON" if game.cheat_vision else "OFF"
        draw_text(10, top - 90, f"CHEAT MODE  (cheat vision: {vision})", color=(1.0, 0.85, 0.0))

    # Controls hint on a dark strip along the bottom edge
    glColor3f(0.1, 0.1, 0.1)
    glBegin(GL_QUADS)
    glVertex2f(0, 0)
    glVertex2f(window_width, 0)
    glVertex2f(window_width, 32)
    glVertex2f(0, 32)
    glEnd()
    draw_text(10, 12, CONTROLS_HINT, GLUT_BITMAP_HELVETICA_12, color=(0.9, 0.9, 0.9))

    if game.game_over:
        banner_y = window_height * 0.68
        draw_centered_text(banner_y, "GAME OVER!", GLUT_BITMAP_TIMES_ROMAN_24, (1.0, 0.3, 0.3))
        draw_centered_text(banner_y - 35, f"Final score: {game.score}   -   Press R to Restart")

    end_2d()
    glEnable(GL_DEPTH_TEST)


def setup_camera():
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(FOV_Y, window_width / window_height, 1.0, 3000)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    if game.first_person:
        rad = math.radians(game.first_person_angle())
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        eye_z = 100
        gluLookAt(game.player_x + cos_a * 30, game.player_y + sin_a * 30, eye_z,
                  game.player_x + cos_a * 100, game.player_y + sin_a * 100, eye_z,
                  0, 0, 1)
    else:
        # Orbit around the arena center.
        rad = math.radians(game.cam_angle)
        gluLookAt(math.cos(rad) * CAM_RADIUS, math.sin(rad) * CAM_RADIUS, game.cam_height,
                  0, 0, 0,
                  0, 0, 1)


# ---------------------------------------------------------------------------
# GLUT callbacks
# ---------------------------------------------------------------------------

def on_display():
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    setup_camera()

    draw_environment()
    draw_enemies()
    draw_bullets()
    draw_player()
    draw_hud()

    glutSwapBuffers()


def on_reshape(width, height):
    global window_width, window_height
    window_width, window_height = max(width, 1), max(height, 1)
    glViewport(0, 0, window_width, window_height)


def on_idle():
    """Run the simulation in fixed steps for however much real time has passed, then redraw."""
    global last_frame_time, time_accumulator
    now = time.perf_counter()
    if last_frame_time is not None:
        time_accumulator += min(now - last_frame_time, MAX_FRAME_TIME)
    last_frame_time = now

    while time_accumulator >= SIM_STEP:
        game.update(SIM_STEP)
        time_accumulator -= SIM_STEP

    glutPostRedisplay()


def on_keyboard(key, x, y):
    key = key.lower()
    if key == b'w':
        game.move_player(1)
    elif key == b's':
        game.move_player(-1)
    elif key == b'a':
        game.turn_player(PLAYER_TURN)
    elif key == b'd':
        game.turn_player(-PLAYER_TURN)
    elif key == b'c':
        game.toggle_cheat_mode()
    elif key == b'v':
        game.toggle_cheat_vision()
    elif key == b'r':
        game.reset()
        print("--- GAME RESTARTED ---")
    elif key == b'\x1b':   # Esc
        quit_game()


def on_special_key(key, x, y):
    if key == GLUT_KEY_UP:
        game.cam_height = min(game.cam_height + 10, CAM_HEIGHT_MAX)
    elif key == GLUT_KEY_DOWN:
        game.cam_height = max(game.cam_height - 10, CAM_HEIGHT_MIN)
    elif key == GLUT_KEY_LEFT:
        game.cam_angle -= 5
    elif key == GLUT_KEY_RIGHT:
        game.cam_angle += 5


def on_mouse(button, state, x, y):
    if state != GLUT_DOWN:
        return
    if button == GLUT_LEFT_BUTTON:
        game.fire_bullet()
    elif button == GLUT_RIGHT_BUTTON:
        game.first_person = not game.first_person


def quit_game():
    if bool(glutLeaveMainLoop):    # freeglut: return from glutMainLoop cleanly
        glutLeaveMainLoop()
    else:
        raise SystemExit


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def init_window():
    global quadric
    glutInit()
    glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH)
    glutInitWindowSize(WINDOW_WIDTH, WINDOW_HEIGHT)
    glutInitWindowPosition(0, 0)
    glutCreateWindow(WINDOW_TITLE)
    if bool(glutSetOption):
        # freeglut: closing the window returns from glutMainLoop instead of killing the process.
        glutSetOption(GLUT_ACTION_ON_WINDOW_CLOSE, GLUT_ACTION_GLUTMAINLOOP_RETURNS)

    glEnable(GL_DEPTH_TEST)
    quadric = gluNewQuadric()

    glutDisplayFunc(on_display)
    glutReshapeFunc(on_reshape)
    glutKeyboardFunc(on_keyboard)
    glutSpecialFunc(on_special_key)
    glutMouseFunc(on_mouse)
    glutIdleFunc(on_idle)


def main():
    init_window()
    print("--- GAME STARTED ---")
    glutMainLoop()
    print(f"Thanks for playing! Final score: {game.score}")


if __name__ == "__main__":
    main()
