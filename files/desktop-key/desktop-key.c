// SPDX-FileCopyrightText: 2026 Sam Simons
// SPDX-License-Identifier: MIT
// Fixed clipboard chords over Wayland, using a full evdev keymap (not wtype's
// dense synthetic codes). No daemon, device permissions, or clipboard reads.
#define _GNU_SOURCE
#include <linux/input-event-codes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "virtual-keyboard-client.h"

static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *manager;
static void global(void *data, struct wl_registry *registry, uint32_t id,
                   const char *interface, uint32_t version) {
    (void)data; (void)version;
    if (!strcmp(interface, wl_seat_interface.name) && !seat)
        seat = wl_registry_bind(registry, id, &wl_seat_interface, 1);
    else if (!strcmp(interface, zwp_virtual_keyboard_manager_v1_interface.name))
        manager = wl_registry_bind(registry, id, &zwp_virtual_keyboard_manager_v1_interface, 1);
}
static void removed(void *data, struct wl_registry *registry, uint32_t id) {
    (void)data; (void)registry; (void)id;
}
static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}
int main(int argc, char **argv) {
    uint32_t key = 0, modifier_key = KEY_LEFTCTRL;
    const char *modifier = XKB_MOD_NAME_CTRL;
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "copy")) key = KEY_C;
    else if (!strcmp(argv[1], "cut")) key = KEY_X;
    else if (!strcmp(argv[1], "paste")) key = KEY_V;
    else if (!strcmp(argv[1], "terminal-copy")) key = KEY_INSERT;
    else if (!strcmp(argv[1], "terminal-paste")) {
        key = KEY_INSERT; modifier_key = KEY_LEFTSHIFT; modifier = XKB_MOD_NAME_SHIFT;
    } else return 2;

    struct wl_display *display = wl_display_connect(NULL);
    if (!display) { fputs("Wayland connection unavailable\n", stderr); return 1; }
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener listener = {global, removed};
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !seat || !manager) return 1;
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (!context) return 1;
    struct xkb_rule_names names = {.rules="evdev", .model="pc105", .layout="us", .variant="", .options=""};
    struct xkb_keymap *keymap = xkb_keymap_new_from_names(context, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!keymap) return 1;
    char *text = xkb_keymap_get_as_string(keymap, XKB_KEYMAP_FORMAT_TEXT_V1);
    if (!text) return 1;
    size_t length = strlen(text) + 1;
    int fd = memfd_create("shaffy-clipboard-keymap", MFD_CLOEXEC);
    if (fd < 0 || write(fd, text, length) != (ssize_t)length) return 1;
    struct zwp_virtual_keyboard_v1 *keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
    zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, length);
    if (wl_display_roundtrip(display) < 0) return 1;
    close(fd);
    free(text);
    xkb_mod_index_t index = xkb_keymap_mod_get_index(keymap, modifier);
    if (index == XKB_MOD_INVALID || index >= 32) return 1;
    zwp_virtual_keyboard_v1_key(keyboard, now_ms(), modifier_key, WL_KEYBOARD_KEY_STATE_PRESSED);
    zwp_virtual_keyboard_v1_modifiers(keyboard, 1u << index, 0, 0, 0);
    zwp_virtual_keyboard_v1_key(keyboard, now_ms(), key, WL_KEYBOARD_KEY_STATE_PRESSED);
    zwp_virtual_keyboard_v1_key(keyboard, now_ms(), key, WL_KEYBOARD_KEY_STATE_RELEASED);
    zwp_virtual_keyboard_v1_key(keyboard, now_ms(), modifier_key, WL_KEYBOARD_KEY_STATE_RELEASED);
    zwp_virtual_keyboard_v1_modifiers(keyboard, 0, 0, 0, 0);
    int result = wl_display_roundtrip(display);
    zwp_virtual_keyboard_v1_destroy(keyboard);
    wl_display_flush(display);
    xkb_keymap_unref(keymap);
    xkb_context_unref(context);
    wl_display_disconnect(display);
    return result < 0 ? 1 : 0;
}
