/**
 * Smart-Clips AI Studio — Frontend Controller (Alpine.js)
 */
function smartClipsApp() {
  return {
    activeTab: 'create',
    health: {
      gemini_configured: false,
      tiktok_configured: false,
      instagram_configured: false,
      facecam_presets: {},
    },
    toast: { show: false, message: '', type: 'success' },

    // Create VOD/Live Form
    form: {
      youtube_url: '',
      content_type: 'auto',
      layout_mode: 'auto',
      subtitle_style: 'karaoke_yellow',
      facecam_preset: 'auto',
      clip_count: 5,
      min_duration: 20,
      max_duration: 60,
      hook_banner: true,
      max_video_minutes: '',
    },
    inspecting: false,
    inspectedInfo: null,
    submitting: false,

    // Live Stream Watcher Form & List
    liveForm: {
      youtube_url: '',
      content_type: 'gaming',
      layout_mode: 'gaming_split',
      subtitle_style: 'karaoke_yellow',
      facecam_preset: 'auto',
      chunk_seconds: 180,
      auto_clip_threshold: 75,
      auto_post: false,
    },
    liveMonitors: [],

    // Projects & Clips
    projects: [],
    selectedProject: null,
    selectedClips: [],
    publishingId: null,

    // Settings
    settings: {},

    // Edit Clip Modal + Visual Facecam Crop Dragging
    editModal: {
      open: false,
      clipId: null,
      title: '',
      hook_text: '',
      caption: '',
      hashtags: '',
      start_time: 0,
      end_time: 30,
      layout_mode: 'podcast',
      subtitle_style: 'karaoke_yellow',
      facecam_box: { x: 0.72, y: 0.62, w: 0.26, h: 0.34 },
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
    },

    pollTimer: null,

    async init() {
      await Promise.all([
        this.fetchHealth(),
        this.fetchProjects(),
        this.fetchLiveMonitors(),
      ]);

      // Poll active jobs, rendering clips, and live monitors every 3 seconds
      this.pollTimer = setInterval(async () => {
        const hasActiveProj = this.projects.some(p => ['queued', 'processing', 'live_watching'].includes(p.status));
        const hasRenderingClip = this.selectedClips.some(c => c.status === 'rendering');
        const hasActiveLive = this.liveMonitors.some(m => m.status === 'running');

        if (hasActiveProj || hasRenderingClip) {
          await this.fetchProjects();
          if (this.selectedProject) {
            await this.openProject(this.selectedProject.id, true);
          }
        }
        if (hasActiveLive || this.activeTab === 'live') {
          await this.fetchLiveMonitors();
        }
      }, 3000);
    },

    notify(message, type = 'success') {
      this.toast = { show: true, message, type };
      setTimeout(() => { this.toast.show = false; }, 4500);
    },

    activeLiveCount() {
      return this.liveMonitors.filter(m => m.status === 'running').length;
    },

    formatDuration(sec) {
      if (!sec || isNaN(sec)) return '0:00';
      const total = Math.max(0, Math.floor(sec));
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
      return `${m}:${String(s).padStart(2, '0')}`;
    },

    async fetchHealth() {
      try {
        const res = await fetch('/api/health');
        this.health = await res.json();
      } catch (e) {}
    },

    async fetchProjects() {
      try {
        const res = await fetch('/api/projects');
        const data = await res.json();
        this.projects = data.projects || [];
      } catch (e) {}
    },

    async fetchLiveMonitors() {
      try {
        const res = await fetch('/api/live-monitors');
        const data = await res.json();
        this.liveMonitors = data.monitors || [];
      } catch (e) {}
    },

    async fetchSettings() {
      try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        this.settings = data.settings || {};
      } catch (e) {}
    },

    async saveSettings() {
      try {
        const res = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ settings: this.settings }),
        });
        if (!res.ok) throw new Error('Gagal menyimpan pengaturan');
        await this.fetchHealth();
        this.notify('Pengaturan AI & Sosmed berhasil disimpan!');
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async inspectUrl() {
      if (!this.form.youtube_url.trim()) return;
      this.inspecting = true;
      this.inspectedInfo = null;
      try {
        const res = await fetch('/api/inspect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ youtube_url: this.form.youtube_url.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'URL tidak valid');
        this.inspectedInfo = data.info;
        if (this.form.layout_mode === 'auto' && data.info.suggested_layout) {
          this.form.layout_mode = data.info.suggested_layout;
        }
        this.notify(`Video ditemukan: ${data.info.title.slice(0, 45)}`);
      } catch (e) {
        this.notify(e.message, 'error');
      } finally {
        this.inspecting = false;
      }
    },

    async createProject() {
      if (!this.form.youtube_url.trim()) return;
      this.submitting = true;
      try {
        const payload = {
          youtube_url: this.form.youtube_url.trim(),
          content_type: this.form.layout_mode === 'gaming_split' ? 'gaming' : (this.form.layout_mode === 'podcast' ? 'podcast' : 'auto'),
          layout_mode: this.form.layout_mode,
          subtitle_style: this.form.subtitle_style,
          facecam_preset: this.form.facecam_preset,
          clip_count: Number(this.form.clip_count) || 5,
          min_duration: Number(this.form.min_duration) || 20,
          max_duration: Number(this.form.max_duration) || 60,
          hook_banner: Boolean(this.form.hook_banner),
          max_video_minutes: this.form.max_video_minutes ? Number(this.form.max_video_minutes) : null,
        };
        const res = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Gagal membuat proyek');

        this.notify('Pipeline dimulai! Klip sedang diproses di background...');
        await this.fetchProjects();
        this.activeTab = 'projects';
        await this.openProject(data.project.id);
      } catch (e) {
        this.notify(e.message, 'error');
      } finally {
        this.submitting = false;
      }
    },

    async startLiveWatcherFromForm() {
      this.liveForm.youtube_url = this.form.youtube_url.trim();
      this.liveForm.layout_mode = this.form.layout_mode === 'auto' ? 'gaming_split' : this.form.layout_mode;
      this.liveForm.facecam_preset = this.form.facecam_preset;
      this.liveForm.subtitle_style = this.form.subtitle_style;
      this.activeTab = 'live';
      await this.startLiveMonitor();
    },

    async startLiveMonitor() {
      if (!this.liveForm.youtube_url.trim()) return;
      try {
        const res = await fetch('/api/live-monitors', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...this.liveForm,
            content_type: this.liveForm.layout_mode === 'podcast' ? 'podcast' : 'gaming',
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Gagal memulai live monitor');
        this.liveForm.youtube_url = '';
        await this.fetchLiveMonitors();
        this.notify('🔴 Real-Time Live Watcher aktif! Klip akan muncul otomatis.');
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async stopLiveMonitor(monitorId) {
      try {
        await fetch(`/api/live-monitors/${monitorId}/stop`, { method: 'POST' });
        await this.fetchLiveMonitors();
        this.notify('Pemantauan Live Stream dihentikan.');
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async openProject(projectId, silent = false) {
      try {
        const res = await fetch(`/api/projects/${projectId}`);
        if (!res.ok) {
          if (!silent) this.notify('Proyek belum siap atau tidak ditemukan', 'error');
          return;
        }
        const data = await res.json();
        this.selectedProject = data.project;
        this.selectedClips = data.clips || [];
        if (!silent) this.activeTab = 'projects';
      } catch (e) {
        if (!silent) this.notify('Gagal memuat detail proyek', 'error');
      }
    },

    async deleteProject(projectId) {
      if (!confirm('Hapus proyek beserta semua klip videonya?')) return;
      await fetch(`/api/projects/${projectId}`, { method: 'DELETE' });
      if (this.selectedProject?.id === projectId) {
        this.selectedProject = null;
        this.selectedClips = [];
      }
      await this.fetchProjects();
      this.notify('Proyek dihapus.');
    },

    async retryProject(projectId) {
      try {
        const res = await fetch(`/api/projects/${projectId}/retry`, { method: 'POST' });
        if (!res.ok) throw new Error('Gagal memulai ulang proyek');
        this.notify('⚡ Turbo Mode dimulai! Hanya mengunduh audio ringan + klip HD terpilih.');
        await this.fetchProjects();
        await this.openProject(projectId, true);
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async reanalyzeProject(projectId) {
      try {
        const res = await fetch(`/api/projects/${projectId}/reanalyze`, { method: 'POST' });
        if (!res.ok) throw new Error('Gagal memulai analisis ulang');
        this.notify('🎯 AI Viral Director V2 sedang mencari ulang momen & Hook 0:00 terbaik...');
        await this.fetchProjects();
        await this.openProject(projectId, true);
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async deleteClip(clipId) {
      if (!confirm('Hapus klip ini?')) return;
      await fetch(`/api/clips/${clipId}`, { method: 'DELETE' });
      if (this.selectedProject) await this.openProject(this.selectedProject.id, true);
      this.notify('Klip dihapus.');
    },

    async copyCaption(clip) {
      const fullText = `${clip.caption || clip.title}\n\n${clip.hashtags || ''}`.trim();
      try {
        await navigator.clipboard.writeText(fullText);
        this.notify('Caption + Hashtag berhasil disalin ke clipboard!');
      } catch (e) {
        this.notify('Gagal menyalin otomatis, silakan blok teks manual.', 'error');
      }
    },

    async publishClip(clip, platforms) {
      this.publishingId = clip.id;
      try {
        // Save any edited caption/hashtags first
        await fetch(`/api/clips/${clip.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ caption: clip.caption, hashtags: clip.hashtags, rerender: false }),
        });

        const res = await fetch(`/api/clips/${clip.id}/publish`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ platforms }),
        });
        const data = await res.json();

        const messages = [];
        let hasError = false;
        for (const [plat, r] of Object.entries(data.results || {})) {
          if (r.ok) {
            messages.push(`${plat.toUpperCase()}: Berhasil diposting!`);
          } else {
            hasError = true;
            messages.push(`${plat.toUpperCase()}: ${r.error}`);
          }
        }
        this.notify(messages.join(' | ') || 'Proses posting selesai', hasError ? 'error' : 'success');
        if (this.selectedProject) await this.openProject(this.selectedProject.id, true);
      } catch (e) {
        this.notify('Gagal menghubungi API posting: ' + e.message, 'error');
      } finally {
        this.publishingId = null;
      }
    },

    // ---- Edit Clip & Interactive Visual Facecam Cropper ----

    openEditModal(clip) {
      const box = clip.facecam_box || { x: 0.72, y: 0.62, w: 0.26, h: 0.34 };
      this.editModal = {
        open: true,
        clipId: clip.id,
        title: clip.title || '',
        hook_text: clip.hook_text || '',
        caption: clip.caption || '',
        hashtags: clip.hashtags || '',
        start_time: Number(clip.start_time) || 0,
        end_time: Number(clip.end_time) || 30,
        layout_mode: clip.layout_mode || 'podcast',
        subtitle_style: clip.subtitle_style || 'karaoke_yellow',
        facecam_box: { x: box.x, y: box.y, w: box.w, h: box.h },
        dragging: false,
        dragStartX: 0,
        dragStartY: 0,
      };
    },

    applyFacecamPreset(presetKey) {
      const p = this.health.facecam_presets?.[presetKey];
      if (p) {
        this.editModal.facecam_box = { x: p.x, y: p.y, w: p.w, h: p.h };
      }
    },

    startCropDrag(ev) {
      const rect = this.$refs.cropStage.getBoundingClientRect();
      const nx = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
      this.editModal.dragging = true;
      this.editModal.dragStartX = nx;
      this.editModal.dragStartY = ny;
      this.editModal.facecam_box = { x: nx, y: ny, w: 0.05, h: 0.05 };
    },

    moveCropDrag(ev) {
      if (!this.editModal.dragging) return;
      const rect = this.$refs.cropStage.getBoundingClientRect();
      const nx = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height));
      const x = Math.min(this.editModal.dragStartX, nx);
      const y = Math.min(this.editModal.dragStartY, ny);
      const w = Math.max(0.08, Math.abs(nx - this.editModal.dragStartX));
      const h = Math.max(0.08, Math.abs(ny - this.editModal.dragStartY));
      this.editModal.facecam_box = {
        x: Number(x.toFixed(3)),
        y: Number(y.toFixed(3)),
        w: Number(Math.min(1 - x, w).toFixed(3)),
        h: Number(Math.min(1 - y, h).toFixed(3)),
      };
    },

    endCropDrag() {
      this.editModal.dragging = false;
    },

    async saveClipEdit(rerender) {
      const id = this.editModal.clipId;
      if (!id) return;
      try {
        const res = await fetch(`/api/clips/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: this.editModal.title,
            hook_text: this.editModal.hook_text,
            caption: this.editModal.caption,
            hashtags: this.editModal.hashtags,
            start_time: Number(this.editModal.start_time),
            end_time: Number(this.editModal.end_time),
            layout_mode: this.editModal.layout_mode,
            subtitle_style: this.editModal.subtitle_style,
            facecam_box: this.editModal.facecam_box,
            rerender,
          }),
        });
        if (!res.ok) throw new Error('Gagal memperbarui klip');
        this.editModal.open = false;
        this.notify(rerender ? 'Sedang merender ulang video 9:16...' : 'Perubahan disimpan!');
        if (this.selectedProject) await this.openProject(this.selectedProject.id, true);
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },
  };
}

