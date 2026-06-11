#!/bin/bash
# Download the MTP drafters referenced by models.ini into the global
# huggingface cache, and link that cache as ./huggingface so that models.ini
# (which cannot expand ~ or $HOME) can reach it via a relative path.
set -o errexit
set -o nounset

hf download unsloth/gemma-4-E2B-it-qat-GGUF mtp-gemma-4-E2B-it.gguf
hf download unsloth/gemma-4-E4B-it-qat-GGUF mtp-gemma-4-E4B-it.gguf
hf download unsloth/gemma-4-12B-it-qat-GGUF mtp-gemma-4-12B-it.gguf
hf download unsloth/gemma-4-31B-it-qat-GGUF mtp-gemma-4-31B-it.gguf

if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # MSYS bash can't create symlinks. Use an NTFS junction instead, which
  # needs no admin rights. hf resolves the cache through USERPROFILE,
  # which may differ from MSYS bash's HOME.
  if [ ! -e huggingface ]; then
    # MSYS argument conversion mangles the target path when it's embedded in a
    # quoted cmd string; build it with cygpath and disable conversion instead.
    MSYS2_ARG_CONV_EXCL='*' cmd /c mklink /J huggingface \
      "$(cygpath -w "$USERPROFILE/.cache/huggingface")"
  fi
else
  ln -fs ~/.cache/huggingface
fi
