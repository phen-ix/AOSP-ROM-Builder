/*
 * Standalone Static ARM64 Recovery Updater for joyeuse / miatoll (SM6250)
 * Zero external shell/unzip/zstd runtime dependencies:
 *   - Direct Linux kernel execve() (no #!/sbin/sh required on SAR recoveries)
 *   - Full 64-bit ZIP32 + ZIP64 parser (_FILE_OFFSET_BITS=64)
 *   - Streaming Zstandard decompression (super.img.zst -> /dev/block/.../super)
 *   - Automatic detach-unmount + BLKROSET=0 unlock on dynamic super block device
 */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include "zstd.h"

#define IO_CHUNK_SIZE (4 * 1024 * 1024)

static int g_cmd_fd = -1;

static void ui_print(const char *fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    if (g_cmd_fd >= 0) {
        dprintf(g_cmd_fd, "ui_print %s\nui_print\n", msg);
    } else {
        printf("%s\n", msg);
        fflush(stdout);
    }
}

static void set_progress(double frac) {
    if (g_cmd_fd >= 0) {
        if (frac < 0.0) frac = 0.0;
        if (frac > 1.0) frac = 1.0;
        dprintf(g_cmd_fd, "set_progress %.4f\n", frac);
    }
}

static void show_progress(double portion, int seconds) {
    if (g_cmd_fd >= 0) {
        dprintf(g_cmd_fd, "show_progress %.4f %d\n", portion, seconds);
    }
}

static uint16_t rd_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t rd_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t rd_le64(const uint8_t *p) {
    return (uint64_t)rd_le32(p) | ((uint64_t)rd_le32(p + 4) << 32);
}

typedef struct {
    char name[256];
    uint16_t method;
    uint64_t comp_size;
    uint64_t uncomp_size;
    uint64_t data_offset;
} zip_entry_t;

typedef struct {
    int fd;
    uint64_t file_size;
    zip_entry_t *entries;
    size_t count;
} zip_archive_t;

static int zip_open(const char *path, zip_archive_t *za) {
    memset(za, 0, sizeof(*za));
    za->fd = open(path, O_RDONLY | O_CLOEXEC);
    if (za->fd < 0) return -1;

    struct stat st;
    if (fstat(za->fd, &st) != 0 || st.st_size < 22) {
        close(za->fd);
        return -1;
    }
    za->file_size = (uint64_t)st.st_size;

    size_t tail_len = za->file_size < 65558 ? (size_t)za->file_size : 65558;
    uint8_t *tail = malloc(tail_len);
    if (!tail) {
        close(za->fd);
        return -1;
    }
    if (pread(za->fd, tail, tail_len, (off_t)(za->file_size - tail_len)) != (ssize_t)tail_len) {
        free(tail);
        close(za->fd);
        return -1;
    }

    ssize_t eocd_pos = -1;
    for (ssize_t i = (ssize_t)tail_len - 22; i >= 0; i--) {
        if (tail[i] == 0x50 && tail[i + 1] == 0x4b && tail[i + 2] == 0x05 && tail[i + 3] == 0x06) {
            eocd_pos = i;
            break;
        }
    }
    if (eocd_pos < 0) {
        free(tail);
        close(za->fd);
        return -1;
    }

    uint64_t cd_count = rd_le16(tail + eocd_pos + 10);
    uint64_t cd_size = rd_le32(tail + eocd_pos + 12);
    uint64_t cd_offset = rd_le32(tail + eocd_pos + 16);

    /* Check ZIP64 EOCD Locator right before EOCD */
    if (eocd_pos >= 20) {
        const uint8_t *loc = tail + eocd_pos - 20;
        if (loc[0] == 0x50 && loc[1] == 0x4b && loc[2] == 0x06 && loc[3] == 0x07) {
            uint64_t z64_off = rd_le64(loc + 8);
            uint8_t z64_hdr[56];
            if (pread(za->fd, z64_hdr, sizeof(z64_hdr), (off_t)z64_off) == (ssize_t)sizeof(z64_hdr) &&
                z64_hdr[0] == 0x50 && z64_hdr[1] == 0x4b && z64_hdr[2] == 0x06 && z64_hdr[3] == 0x06) {
                cd_count = rd_le64(z64_hdr + 32);
                cd_size = rd_le64(z64_hdr + 40);
                cd_offset = rd_le64(z64_hdr + 48);
            }
        }
    }
    free(tail);

    if (cd_size == 0 || cd_offset + cd_size > za->file_size || cd_count > 4096) {
        close(za->fd);
        return -1;
    }

    uint8_t *cd = malloc((size_t)cd_size);
    if (!cd) {
        close(za->fd);
        return -1;
    }
    if (pread(za->fd, cd, (size_t)cd_size, (off_t)cd_offset) != (ssize_t)cd_size) {
        free(cd);
        close(za->fd);
        return -1;
    }

    za->entries = calloc((size_t)cd_count, sizeof(zip_entry_t));
    if (!za->entries) {
        free(cd);
        close(za->fd);
        return -1;
    }

    size_t pos = 0, idx = 0;
    while (pos + 46 <= cd_size && idx < cd_count) {
        if (!(cd[pos] == 0x50 && cd[pos + 1] == 0x4b && cd[pos + 2] == 0x01 && cd[pos + 3] == 0x02)) {
            break;
        }
        uint16_t method = rd_le16(cd + pos + 10);
        uint32_t csz32 = rd_le32(cd + pos + 20);
        uint32_t usz32 = rd_le32(cd + pos + 24);
        uint16_t fn_len = rd_le16(cd + pos + 28);
        uint16_t ex_len = rd_le16(cd + pos + 30);
        uint16_t cm_len = rd_le16(cd + pos + 32);
        uint32_t lhoff32 = rd_le32(cd + pos + 42);

        if (pos + 46 + fn_len + ex_len + cm_len > cd_size) break;

        uint64_t csz = csz32, usz = usz32, lhoff = lhoff32;
        const uint8_t *extra = cd + pos + 46 + fn_len;
        size_t ep = 0;
        while (ep + 4 <= ex_len) {
            uint16_t eid = rd_le16(extra + ep);
            uint16_t esz = rd_le16(extra + ep + 2);
            if (ep + 4 + esz > ex_len) break;
            if (eid == 0x0001) {
                size_t eo = ep + 4;
                if (usz32 == 0xFFFFFFFFU && eo + 8 <= ep + 4 + esz) {
                    usz = rd_le64(extra + eo);
                    eo += 8;
                }
                if (csz32 == 0xFFFFFFFFU && eo + 8 <= ep + 4 + esz) {
                    csz = rd_le64(extra + eo);
                    eo += 8;
                }
                if (lhoff32 == 0xFFFFFFFFU && eo + 8 <= ep + 4 + esz) {
                    lhoff = rd_le64(extra + eo);
                    eo += 8;
                }
            }
            ep += 4 + esz;
        }

        uint8_t lhdr[30];
        if (pread(za->fd, lhdr, 30, (off_t)lhoff) == 30 &&
            lhdr[0] == 0x50 && lhdr[1] == 0x4b && lhdr[2] == 0x03 && lhdr[3] == 0x04) {
            uint16_t l_fn = rd_le16(lhdr + 26);
            uint16_t l_ex = rd_le16(lhdr + 28);
            size_t copy_len = fn_len < sizeof(za->entries[idx].name) - 1 ? fn_len : sizeof(za->entries[idx].name) - 1;
            memcpy(za->entries[idx].name, cd + pos + 46, copy_len);
            za->entries[idx].name[copy_len] = '\0';
            za->entries[idx].method = method;
            za->entries[idx].comp_size = csz;
            za->entries[idx].uncomp_size = usz;
            za->entries[idx].data_offset = lhoff + 30ULL + l_fn + l_ex;
            idx++;
        }
        pos += 46 + fn_len + ex_len + cm_len;
    }

    za->count = idx;
    free(cd);
    return 0;
}

static const zip_entry_t *zip_find(const zip_archive_t *za, const char *name) {
    for (size_t i = 0; i < za->count; i++) {
        if (strcmp(za->entries[i].name, name) == 0) {
            return &za->entries[i];
        }
    }
    return NULL;
}

static void zip_close(zip_archive_t *za) {
    if (za->entries) free(za->entries);
    if (za->fd >= 0) close(za->fd);
    memset(za, 0, sizeof(*za));
    za->fd = -1;
}

static int write_all(int fd, const uint8_t *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(fd, buf + off, len - off);
        if (w < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        off += (size_t)w;
    }
    return 0;
}

static int find_block_device(const char *part, char *out_path, size_t out_sz) {
    const char *override_dir = getenv("MIATOLL_BLOCK_DIR");
    const char *bases[] = {
        override_dir,
        "/dev/block/bootdevice/by-name",
        "/dev/block/platform/soc/1d84000.ufshc/by-name",
        "/dev/block/by-name",
    };
    const char *suffixes[] = {"", "_a"};

    size_t base_count = override_dir ? 1 : sizeof(bases) / sizeof(bases[0]);
    for (size_t b = 0; b < base_count; b++) {
        if (!bases[b] || !bases[b][0]) continue;
        for (size_t s = 0; s < sizeof(suffixes) / sizeof(suffixes[0]); s++) {
            snprintf(out_path, out_sz, "%s/%s%s", bases[b], part, suffixes[s]);
            if (access(out_path, F_OK) == 0) {
                return 0;
            }
        }
    }
    return -1;
}

static void prepare_dynamic_super_for_write(void) {
    if (getenv("MIATOLL_BLOCK_DIR")) return;

    const char *mnts[] = {
        "/system_root", "/system", "/system_ext", "/product",
        "/vendor", "/odm", "/mi_ext", "/mnt/system",
    };
    for (size_t i = 0; i < sizeof(mnts) / sizeof(mnts[0]); i++) {
        umount2(mnts[i], MNT_DETACH);
    }

    /* Release any recovery dm-linear mappings holding super if dmsetup exists */
    const char *dm_bins[] = {"/system/bin/dmsetup", "/sbin/dmsetup"};
    for (size_t i = 0; i < sizeof(dm_bins) / sizeof(dm_bins[0]); i++) {
        if (access(dm_bins[i], X_OK) == 0) {
            pid_t pid = fork();
            if (pid == 0) {
                int devnull = open("/dev/null", O_RDWR);
                if (devnull >= 0) {
                    dup2(devnull, STDOUT_FILENO);
                    dup2(devnull, STDERR_FILENO);
                    close(devnull);
                }
                execl(dm_bins[i], "dmsetup", "remove_all", "-f", (char *)NULL);
                _exit(0);
            } else if (pid > 0) {
                int status = 0;
                waitpid(pid, &status, 0);
            }
            break;
        }
    }
}

static int open_target_writable(const char *blk_path) {
    int fd = open(blk_path, O_WRONLY | O_CLOEXEC);
    if (fd < 0 && getenv("MIATOLL_BLOCK_DIR")) {
        fd = open(blk_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    }
    if (fd >= 0) {
        int ro_zero = 0;
        ioctl(fd, BLKROSET, &ro_zero);
    }
    return fd;
}

static int flash_raw_stored_entry(const zip_archive_t *za, const char *entry_name, const char *part_name, bool optional) {
    const zip_entry_t *ent = zip_find(za, entry_name);
    if (!ent) {
        if (optional) return 0;
        ui_print("! خطأ: الملف %s غير موجود داخل حزمة التفليش!", entry_name);
        return -1;
    }
    if (ent->method != 0) {
        ui_print("! خطأ: الملف %s يجب أن يكون مخزناً بدون ضغط مزدوج (method=%u)", entry_name, ent->method);
        return -1;
    }

    char blk[512];
    if (find_block_device(part_name, blk, sizeof(blk)) != 0) {
        if (optional) return 0;
        ui_print("! خطأ: لم يتم العثور على بارتيشن %s في الجهاز!", part_name);
        return -1;
    }

    ui_print("- جاري تفليش %s (%" PRIu64 " KB)...", part_name, ent->comp_size / 1024ULL);
    int out_fd = open_target_writable(blk);
    if (out_fd < 0) {
        ui_print("! خطأ: تعذر فتح %s للكتابة (%s)", blk, strerror(errno));
        return -1;
    }

    uint8_t *buf = malloc(IO_CHUNK_SIZE);
    if (!buf) {
        close(out_fd);
        return -1;
    }

    uint64_t rem = ent->comp_size;
    uint64_t off = ent->data_offset;
    while (rem > 0) {
        size_t chunk = rem > IO_CHUNK_SIZE ? IO_CHUNK_SIZE : (size_t)rem;
        ssize_t r = pread(za->fd, buf, chunk, (off_t)off);
        if (r != (ssize_t)chunk) {
            ui_print("! خطأ في قراءة %s من الأرشيف!", entry_name);
            free(buf);
            close(out_fd);
            return -1;
        }
        if (write_all(out_fd, buf, chunk) != 0) {
            ui_print("! خطأ أثناء الكتابة إلى %s (%s)", blk, strerror(errno));
            free(buf);
            close(out_fd);
            return -1;
        }
        off += chunk;
        rem -= chunk;
    }

    fsync(out_fd);
    close(out_fd);
    free(buf);
    return 0;
}

static int flash_zstd_entry(const zip_archive_t *za, const char *entry_name, const char *part_name, double prog_base, double prog_span) {
    const zip_entry_t *ent = zip_find(za, entry_name);
    if (!ent) {
        ui_print("! خطأ: الملف %s غير موجود داخل حزمة التفليش!", entry_name);
        return -1;
    }
    if (ent->method != 0) {
        ui_print("! خطأ: %s يجب أن يكون مخزناً بصيغة STORED داخل الـ ZIP (method=%u)", entry_name, ent->method);
        return -1;
    }

    char blk[512];
    if (find_block_device(part_name, blk, sizeof(blk)) != 0) {
        ui_print("! خطأ: لم يتم العثور على مسار البارتيشن %s!", part_name);
        return -1;
    }

    int out_fd = open_target_writable(blk);
    if (out_fd < 0) {
        ui_print("! خطأ: تعذر فتح %s للكتابة (%s)", blk, strerror(errno));
        return -1;
    }

    ZSTD_DCtx *dctx = ZSTD_createDCtx();
    uint8_t *in_buf = malloc(IO_CHUNK_SIZE);
    uint8_t *out_buf = malloc(IO_CHUNK_SIZE);
    if (!dctx || !in_buf || !out_buf) {
        if (dctx) ZSTD_freeDCtx(dctx);
        free(in_buf);
        free(out_buf);
        close(out_fd);
        return -1;
    }

    uint64_t rem = ent->comp_size;
    uint64_t off = ent->data_offset;
    uint64_t total_written = 0;
    int last_pct = -1;

    while (rem > 0) {
        size_t to_read = rem > IO_CHUNK_SIZE ? IO_CHUNK_SIZE : (size_t)rem;
        ssize_t r = pread(za->fd, in_buf, to_read, (off_t)off);
        if (r != (ssize_t)to_read) {
            ui_print("! خطأ أثناء قراءة %s من الأرشيف!", entry_name);
            ZSTD_freeDCtx(dctx);
            free(in_buf);
            free(out_buf);
            close(out_fd);
            return -1;
        }

        ZSTD_inBuffer input = {in_buf, to_read, 0};
        while (input.pos < input.size) {
            ZSTD_outBuffer output = {out_buf, IO_CHUNK_SIZE, 0};
            size_t ret = ZSTD_decompressStream(dctx, &output, &input);
            if (ZSTD_isError(ret)) {
                ui_print("! خطأ في فك ضغط Zstd لـ %s: %s", entry_name, ZSTD_getErrorName(ret));
                ZSTD_freeDCtx(dctx);
                free(in_buf);
                free(out_buf);
                close(out_fd);
                return -1;
            }
            if (output.pos > 0) {
                if (write_all(out_fd, out_buf, output.pos) != 0) {
                    ui_print("! خطأ أثناء كتابة %s (%s)", blk, strerror(errno));
                    ZSTD_freeDCtx(dctx);
                    free(in_buf);
                    free(out_buf);
                    close(out_fd);
                    return -1;
                }
                total_written += output.pos;
            }
        }

        off += to_read;
        rem -= to_read;

        double done_ratio = (double)(ent->comp_size - rem) / (double)(ent->comp_size ? ent->comp_size : 1);
        int pct = (int)(done_ratio * 100.0);
        if (pct >= last_pct + 20) {
            last_pct = pct;
            ui_print("  -> تقدم كتابة %s: %d%% (%" PRIu64 " MB مكتوبة)", part_name, pct, total_written / (1024ULL * 1024ULL));
            set_progress(prog_base + prog_span * done_ratio);
        }
    }

    fsync(out_fd);
    close(out_fd);
    ZSTD_freeDCtx(dctx);
    free(in_buf);
    free(out_buf);
    return 0;
}

static bool verify_miatoll_device(char *detected_out, size_t detected_sz) {
    snprintf(detected_out, detected_sz, "unknown");
    const char *env_hw = getenv("MIATOLL_HWNAME_OVERRIDE");
    if (env_hw && env_hw[0]) {
        snprintf(detected_out, detected_sz, "%s", env_hw);
    } else {
        FILE *fp = fopen("/proc/cmdline", "r");
        if (fp) {
            char cmdline[4096] = {0};
            if (fread(cmdline, 1, sizeof(cmdline) - 1, fp) > 0) {
                const char *keys[] = {"androidboot.hwname=", "androidboot.product.hardware.sku="};
                for (size_t k = 0; k < 2; k++) {
                    char *p = strstr(cmdline, keys[k]);
                    if (p) {
                        p += strlen(keys[k]);
                        size_t n = strcspn(p, " \t\r\n");
                        if (n > 0 && n < detected_sz) {
                            memcpy(detected_out, p, n);
                            detected_out[n] = '\0';
                            break;
                        }
                    }
                }
            }
            fclose(fp);
        }
    }

    const char *allowed[] = {"joyeuse", "curtana", "excalibur", "gram", "miatoll"};
    for (size_t i = 0; i < sizeof(allowed) / sizeof(allowed[0]); i++) {
        if (strcmp(detected_out, allowed[i]) == 0) {
            return true;
        }
    }

    /* Fallback if recovery stripped androidboot.hwname: check SM6250 UFS controller path */
    if (strcmp(detected_out, "unknown") == 0 &&
        access("/dev/block/platform/soc/1d84000.ufshc/by-name/super", F_OK) == 0) {
        snprintf(detected_out, detected_sz, "miatoll-sm6250");
        return true;
    }
    return false;
}

static void remove_dir_contents(const char *dir_path) {
    DIR *d = opendir(dir_path);
    if (!d) return;
    struct dirent *de;
    char child[1024];
    while ((de = readdir(d)) != NULL) {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0) continue;
        snprintf(child, sizeof(child), "%s/%s", dir_path, de->d_name);
        struct stat st;
        if (lstat(child, &st) == 0) {
            if (S_ISDIR(st.st_mode)) {
                remove_dir_contents(child);
                rmdir(child);
            } else {
                unlink(child);
            }
        }
    }
    closedir(d);
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "Usage: %s <api_ver> <status_fd> <zip_file>\n", argv[0]);
        return 2;
    }

    int fd_arg = atoi(argv[2]);
    if (fd_arg > 0) {
        g_cmd_fd = fd_arg;
    }
    const char *zip_path = argv[3];

    ui_print("================================================");
    ui_print("  HyperOS 3.0 Android 16 (joyeuse / miatoll)    ");
    ui_print("  EROFS + uclamp + 32-Bit HAL Bridge Installer  ");
    ui_print("================================================");
    show_progress(1.0, 120);
    set_progress(0.02);

    char hwname[64];
    if (!verify_miatoll_device(hwname, sizeof(hwname))) {
        ui_print("! خطأ: هذا الروم مخصص فقط لعائلة miatoll (joyeuse/curtana/excalibur/gram)!");
        ui_print("! الجهاز المكتشف: %s", hwname);
        return 1;
    }
    ui_print("- الجهاز المتوافق المكتشف: %s", hwname);

    zip_archive_t za;
    if (zip_open(zip_path, &za) != 0) {
        ui_print("! خطأ: تعذر فتح أرشيف التفليش %s", zip_path);
        return 1;
    }

    prepare_dynamic_super_for_write();

    if (flash_raw_stored_entry(&za, "vbmeta.img", "vbmeta", false) != 0 ||
        flash_raw_stored_entry(&za, "vbmeta_system.img", "vbmeta_system", false) != 0 ||
        flash_raw_stored_entry(&za, "vbmeta_vendor.img", "vbmeta_vendor", true) != 0 ||
        flash_raw_stored_entry(&za, "dtbo.img", "dtbo", false) != 0 ||
        flash_raw_stored_entry(&za, "boot.img", "boot", false) != 0) {
        zip_close(&za);
        return 1;
    }
    set_progress(0.15);

    ui_print("- جاري فك وتفليش حاوية النظام super.img.zst (EROFS)...");
    if (flash_zstd_entry(&za, "super.img.zst", "super", 0.15, 0.80) != 0) {
        zip_close(&za);
        return 1;
    }

    if (!getenv("MIATOLL_BLOCK_DIR")) {
        ui_print("- تنظيف ملفات Dalvik/Package Cache المؤقتة...");
        remove_dir_contents("/data/dalvik-cache");
        remove_dir_contents("/data/resource-cache");
        remove_dir_contents("/data/system/package_cache");
    }

    set_progress(1.0);
    zip_close(&za);

    ui_print("================================================");
    ui_print("✅ اكتمل تفليش الروم بنجاح تام بدون أي أخطاء!");
    ui_print("ℹ️ عند التثبيت النظيف لأول مرة قم بعمل Format Data");
    ui_print("================================================");
    return 0;
}
