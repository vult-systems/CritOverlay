/* Crit Overlay glue for libmypaint (exp/shape-tools prototype).
 *
 * A sparse, unbounded MyPaintTiledSurface: tiles are allocated (zeroed, so
 * fully transparent) the first time a dab writes to them, so a stroke's
 * surface costs memory only where it painted and accepts any coordinate.
 * Plus a fast read of a rectangle into Qt's ARGB32_Premultiplied layout,
 * converting MyPaint's 15-bit fixed point premultiplied RGBA.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "mypaint-tiled-surface.h"

#define EXPORT __declspec(dllexport)

typedef struct {
    int tx, ty;
    uint16_t *data;
} Slot;

typedef struct {
    MyPaintTiledSurface parent;
    int ts;              /* tile side, pixels */
    size_t tile_bytes;
    Slot *slots;
    int cap, count;      /* open addressing, cap a power of two */
    uint16_t *zero_tile; /* handed out for reads of missing tiles */
    uint16_t *scratch;   /* handed out for writes we can't allocate */
} CritSurface;

static unsigned hash2(int x, int y) {
    unsigned h = (unsigned)x * 73856093u ^ (unsigned)y * 19349663u;
    return h ^ (h >> 16);
}

static uint16_t *find(CritSurface *s, int tx, int ty) {
    unsigned i = hash2(tx, ty) & (unsigned)(s->cap - 1);
    for (;;) {
        Slot *sl = &s->slots[i];
        if (!sl->data) return NULL;
        if (sl->tx == tx && sl->ty == ty) return sl->data;
        i = (i + 1) & (unsigned)(s->cap - 1);
    }
}

static void put(CritSurface *s, int tx, int ty, uint16_t *data) {
    unsigned i = hash2(tx, ty) & (unsigned)(s->cap - 1);
    while (s->slots[i].data) i = (i + 1) & (unsigned)(s->cap - 1);
    s->slots[i].tx = tx;
    s->slots[i].ty = ty;
    s->slots[i].data = data;
    s->count++;
}

static int grow(CritSurface *s) {
    Slot *old = s->slots;
    int oldcap = s->cap;
    Slot *slots = (Slot *)calloc((size_t)oldcap * 2, sizeof(Slot));
    if (!slots) return 0;
    s->slots = slots;
    s->cap = oldcap * 2;
    s->count = 0;
    for (int i = 0; i < oldcap; i++)
        if (old[i].data) put(s, old[i].tx, old[i].ty, old[i].data);
    free(old);
    return 1;
}

static void tile_start(MyPaintTiledSurface *ts, MyPaintTileRequest *req) {
    CritSurface *s = (CritSurface *)ts;
    uint16_t *t = find(s, req->tx, req->ty);
    if (!t) {
        if (req->readonly) {
            req->buffer = s->zero_tile;
            return;
        }
        if (s->count * 2 >= s->cap && !grow(s)) {
            req->buffer = s->scratch;
            return;
        }
        t = (uint16_t *)calloc(1, s->tile_bytes);
        if (!t) {
            req->buffer = s->scratch;
            return;
        }
        put(s, req->tx, req->ty, t);
    }
    req->buffer = t;
}

static void tile_end(MyPaintTiledSurface *ts, MyPaintTileRequest *req) {
    CritSurface *s = (CritSurface *)ts;
    if (req->buffer == s->scratch) memset(s->scratch, 0, s->tile_bytes);
    if (req->buffer == s->zero_tile) memset(s->zero_tile, 0, s->tile_bytes);
}

static void destroy(MyPaintSurface *surface) {
    CritSurface *s = (CritSurface *)surface;
    mypaint_tiled_surface_destroy(&s->parent);
    for (int i = 0; i < s->cap; i++) free(s->slots[i].data);
    free(s->slots);
    free(s->zero_tile);
    free(s->scratch);
    free(s);
}

EXPORT MyPaintSurface *crit_surface_new(void) {
    CritSurface *s = (CritSurface *)calloc(1, sizeof(CritSurface));
    if (!s) return NULL;
    mypaint_tiled_surface_init(&s->parent, tile_start, tile_end);
    s->parent.parent.destroy = destroy;
    s->ts = s->parent.tile_size;
    s->tile_bytes = (size_t)s->ts * s->ts * 4 * sizeof(uint16_t);
    s->cap = 64;
    s->slots = (Slot *)calloc((size_t)s->cap, sizeof(Slot));
    s->zero_tile = (uint16_t *)calloc(1, s->tile_bytes);
    s->scratch = (uint16_t *)calloc(1, s->tile_bytes);
    if (!s->slots || !s->zero_tile || !s->scratch) {
        destroy((MyPaintSurface *)s);
        return NULL;
    }
    return (MyPaintSurface *)s;
}

static int floordiv(int a, int b) {
    return (a >= 0) ? a / b : -((-a + b - 1) / b);
}

/* Copy the rectangle (x, y, w, h) in surface pixels into dst, a buffer in
 * QImage::Format_ARGB32_Premultiplied (B, G, R, A bytes on little endian)
 * with the given stride. Missing tiles are transparent. */
EXPORT void crit_surface_read(MyPaintSurface *surface, int x, int y, int w, int h,
                              uint8_t *dst, int stride) {
    CritSurface *s = (CritSurface *)surface;
    const int ts = s->ts;
    for (int row = 0; row < h; row++) {
        int py = y + row;
        int ty = floordiv(py, ts);
        int iy = py - ty * ts;
        uint8_t *out = dst + (size_t)row * stride;
        int col = 0;
        while (col < w) {
            int px = x + col;
            int tx = floordiv(px, ts);
            int ix = px - tx * ts;
            int run = ts - ix;
            if (run > w - col) run = w - col;
            uint16_t *t = find(s, tx, ty);
            if (!t) {
                memset(out + (size_t)col * 4, 0, (size_t)run * 4);
            } else {
                const uint16_t *src = t + ((size_t)iy * ts + ix) * 4;
                uint8_t *o = out + (size_t)col * 4;
                for (int k = 0; k < run; k++, src += 4, o += 4) {
                    /* fix15: 1.0 == 1 << 15 */
                    uint32_t r = src[0], g = src[1], b = src[2], a = src[3];
                    o[0] = (uint8_t)((b * 255u + (1u << 14)) >> 15);
                    o[1] = (uint8_t)((g * 255u + (1u << 14)) >> 15);
                    o[2] = (uint8_t)((r * 255u + (1u << 14)) >> 15);
                    o[3] = (uint8_t)((a * 255u + (1u << 14)) >> 15);
                }
            }
            col += run;
        }
    }
}
