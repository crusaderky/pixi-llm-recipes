ln -fs ~/.cache/huggingface
# FIXME broken
# hf download msievers/gemma-4-E2B-it-qat-q4_0-assistant-GGUF gemma-4-E2B-it-qat-assistant-q4_0.gguf
# hf download cascade-tech/gemma-4-E4B-it-qat-q4_0-unquantized-assistant-gguf gemma-4-E4B-it-qat-assistant-q4_k_m.gguf
hf download RachidAR/gemma-4-12B-it-qat-q4_0-MTP-assistant-gguf gemma-4-12b-qat-it-assistant-Q4_0_Q4emb.gguf
hf download RachidAR/gemma-4-31B-it-qat-Q4_0-Q4emb-MTP-assistant-gguf gemma-4-31b-qat-it-assistant-Q4_0-Q4emb.gguf

