// The few llama.cpp calls HomeDocs needs, as plain functions that core/llm.py calls through ctypes.
//
// llama.cpp's own API passes settings structs by value, and their layout changes between versions: copying
// them in Python breaks quietly (and crashes) on an update. Here the compiler reads llama.h, so the layout is
// always right; Python only sees ints, pointers and strings.
//
// One model and one context per handle. One sequence, greedy decoding (the answer should be the model's most
// likely reading of the passages, not a creative one).

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#include "ggml-backend.h"
#include "llama.h"

#if defined(_WIN32)
#define HD_API __declspec(dllexport)
#else
#define HD_API __attribute__((visibility("default")))
#endif

typedef struct {
    struct llama_model *model;
    struct llama_context *ctx;
    const struct llama_vocab *vocab;
    struct llama_sampler *sampler;
    volatile int stop;  // set from another thread: llama.cpp checks it between steps of a decode
} hd_model;

static void quiet(enum ggml_log_level level, const char *text, void *data) {
    (void)level;
    (void)text;
    (void)data;
}

static bool should_abort(void *data) { return ((hd_model *)data)->stop != 0; }

// Load the CPU backends found in lib_dir (the build has one per CPU feature level and picks the best),
// once per process. log: 0 = silent.
HD_API void hd_init(const char *lib_dir, int log) {
    static int done = 0;
    if (done) return;
    done = 1;
    if (!log) llama_log_set(quiet, NULL);
    ggml_backend_load_all_from_path(lib_dir);
    llama_backend_init();
}

// NULL if the file can't be loaded (missing, damaged, too little memory).
HD_API hd_model *hd_load(const char *path, int n_ctx, int n_threads, int n_threads_batch) {
    struct llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    struct llama_model *model = llama_model_load_from_file(path, mp);
    if (!model) return NULL;

    hd_model *h = calloc(1, sizeof(hd_model));
    if (!h) {
        llama_model_free(model);
        return NULL;
    }
    struct llama_context_params cp = llama_context_default_params();
    cp.n_ctx = (uint32_t)n_ctx;
    cp.n_batch = 512;
    cp.n_threads = n_threads;
    cp.n_threads_batch = n_threads_batch;
    cp.no_perf = true;
    cp.abort_callback = should_abort;
    cp.abort_callback_data = h;
    struct llama_context *ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        free(h);
        llama_model_free(model);
        return NULL;
    }
    h->model = model;
    h->ctx = ctx;
    h->vocab = llama_model_get_vocab(model);
    h->sampler = llama_sampler_init_greedy();
    return h;
}

HD_API void hd_free(hd_model *h) {
    if (!h) return;
    llama_sampler_free(h->sampler);
    llama_free(h->ctx);
    llama_model_free(h->model);
    free(h);
}

HD_API int hd_n_ctx(hd_model *h) { return (int)llama_n_ctx(h->ctx); }

// Text to tokens. Returns the count, or -(needed) if out is too small. special: read <|im_start|> and the like
// as the model's control tokens.
HD_API int hd_tokenize(hd_model *h, const char *text, int len, int32_t *out, int max, int special) {
    return llama_tokenize(h->vocab, text, len, out, max, false, special != 0);
}

// Forget everything after the first keep tokens (keep = the instructions, read once and reused).
HD_API void hd_keep(hd_model *h, int keep) {
    llama_memory_seq_rm(llama_get_memory(h->ctx), 0, keep, -1);
    llama_sampler_reset(h->sampler);
}

// Tokens read so far (the next token's position).
HD_API int hd_pos(hd_model *h) { return (int)llama_memory_seq_pos_max(llama_get_memory(h->ctx), 0) + 1; }

// Read tokens, n_batch at a time. 0: done; 2: stopped by hd_stop; anything else: an error.
HD_API int hd_decode(hd_model *h, int32_t *tokens, int n) {
    int batch = (int)llama_n_batch(h->ctx);
    for (int i = 0; i < n; i += batch) {
        int k = n - i < batch ? n - i : batch;
        int r = llama_decode(h->ctx, llama_batch_get_one(tokens + i, k));
        if (r != 0) return r;
    }
    return 0;
}

// The most likely next token after what's been read.
HD_API int32_t hd_next(hd_model *h) { return llama_sampler_sample(h->sampler, h->ctx, -1); }

HD_API int hd_is_end(hd_model *h, int32_t token) { return llama_vocab_is_eog(h->vocab, token) ? 1 : 0; }

// A token's text (UTF-8 bytes; one character may be split over two tokens). Returns the length, or -(needed).
HD_API int hd_piece(hd_model *h, int32_t token, char *buf, int len) {
    return llama_token_to_piece(h->vocab, token, buf, len, 0, false);
}

// Stop the decode in progress (from any thread); hd_go clears it before the next one.
HD_API void hd_stop(hd_model *h) { h->stop = 1; }
HD_API void hd_go(hd_model *h) { h->stop = 0; }
