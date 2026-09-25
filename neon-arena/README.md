# Neon Arena

Reddit-friendly benchmark for models
Credit to https://www.reddit.com/r/LocalLLaMA/comments/1uimjdi/glm_52_q1_s_vs_qwen_27b_q8/

## Usage

1. In `../pixi-recipes/pi-extensions/recipe.yaml`, remove all extensions that may pollute
   the evaluation (like pi-subagents)
2. Run `pi -ns --model <model tag> @prompt.md`
