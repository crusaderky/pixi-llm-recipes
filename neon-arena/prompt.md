Build a 3D arena game as a SINGLE self-contained .html file.

STACK (mandatory):

- Three.js loaded from a CDN (one <script> tag). No other JS libraries,
  no build step.
- All HTML, CSS, and JS in this one file. It must run by opening it
  directly in a browser.

CORE SPEC (mandatory — implement all of this exactly):

1. A flat ground plane forming a bounded arena. The player cannot leave
   its bounds.
2. A player object on the ground. WASD moves it (camera-relative);
   movement has momentum, not instant stop/start.
3. A third-person camera that smoothly follows behind the player.
4. Collectible glowing orbs spawn at random positions. Touching one
   collects it (+10 score) and spawns a new one.
5. Enemy objects spawn at the arena edges and move toward the player.
   Contact with the player costs 1 life.
6. Player starts with 3 lives. A HUD shows score and lives at all times.
7. At 0 lives: a game-over screen showing final score, with a key press
   to restart.
8. Difficulty ramps over time (enemies spawn faster and/or move faster).

STRETCH (strongly encouraged — you will be judged on this):
Beyond the core, make it feel PREMIUM. Lighting, shadows, particles,
juice, smooth camera, satisfying feedback, polished HUD, atmosphere.
Add depth or complexity if it improves the experience. Aim to genuinely
impress — this is evaluated on visual quality and feel, not just
correctness.

RULES:

- Implement the full core before adding stretch features.
- Output the complete, ready-to-run .html file.
