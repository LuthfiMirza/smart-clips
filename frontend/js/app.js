/**
 * Smart-Clips AI — Exact TailAdmin v2 Dashboard Controller (Alpine.js + ApexCharts)
 * Includes native <dialog> light-dismiss fallback, color-scheme meta sync, and safe non-null bindings.
 */
function smartClipsApp() {
  return {
    activeTab: 'overview',
    sidebarCollapsed: false,
    mobileMenuOpen: false,
    darkMode: false,
    searchQuery: '',

    health: {
      gemini_configured: false,
      tiktok_configured: false,
      instagram_configured: false,
      facecam_presets: {},
    },
    toast: { show: false, message: '', type: 'success' },

    // Create Clip Form
    form: {
      youtube_url: '',
      content_type: 'auto',
      layout_mode: 'gaming_split',
      subtitle_style: 'karaoke_yellow',
      facecam_preset: 'auto',
      clip_count: 4,
      min_duration: 25,
      max_duration: 50,
      hook_banner: true,
      max_video_minutes: '',
    },
    inspecting: false,
    inspectedInfo: null,
    submitting: false,

    // Live Stream Monitor
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

    // Projects & Clips State
    projects: [],
    allClips: [],
    clipFilterProjectId: '',
    publishingId: null,

    // Safe non-null Studio Modal Form (prevents Alpine null x-model crash!)
    studioForm: {
      id: '',
      project_id: '',
      title: '',
      hook_text: '',
      caption: '',
      hashtags: '',
      reason: '',
      virality_score: 0,
      start_time: 0,
      end_time: 0,
      duration: 0,
      layout_mode: 'gaming_split',
      subtitle_style: 'karaoke_yellow',
      video_url: '',
      thumbnail_url: '',
      updated_at: '',
      facecam_box: { x: 0.72, y: 0.62, w: 0.26, h: 0.34 },
    },

    // Settings
    settings: {},

    // Edit Modal State
    editModal: {
      clipId: '',
      projectId: '',
      title: '',
      hook_text: '',
      caption: '',
      hashtags: '',
      start_time: 0,
      end_time: 30,
      layout_mode: 'gaming_split',
      subtitle_style: 'karaoke_yellow',
      facecam_box: { x: 0.72, y: 0.62, w: 0.26, h: 0.34 },
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
    },

    barChart: null,
    radialChart: null,
    pollTimer: null,

    async init() {
      // Sync darkMode with stored color-scheme preference (TailAdmin defaults to clean Light Mode)
      try {
        const stored = localStorage.getItem('color-scheme');
        this.darkMode = stored === 'dark';
        this._applyColorScheme(stored);
      } catch (e) {}

      // Safari fallback for <dialog closedby="any"> light-dismiss (modern-web-guidance: dialog)
      this._installDialogLightDismissFallback();

      await Promise.all([
        this.fetchHealth(),
        this.fetchProjects(),
        this.fetchAllClips(),
        this.fetchLiveMonitors(),
        this.fetchSettings(),
      ]);

      this.$nextTick(() => this.renderCharts());

      // Sync theme across browser tabs
      window.addEventListener('storage', (ev) => {
        if (ev.key === 'color-scheme') {
          this.darkMode = ev.newValue === 'dark';
          this._applyColorScheme(ev.newValue);
          this.renderCharts();
        }
      });

      this.pollTimer = setInterval(async () => {
        const hasActiveProj = this.projects.some(p => ['queued', 'processing', 'live_watching'].includes(p.status));
        const hasRenderingClip = this.allClips.some(c => c.status === 'rendering');
        const hasActiveLive = this.liveMonitors.some(m => m.status === 'running');

        if (hasActiveProj || hasRenderingClip) {
          await Promise.all([this.fetchProjects(), this.fetchAllClips()]);
          if (this.activeTab === 'overview') this.renderCharts();
        }
        if (hasActiveLive || this.activeTab === 'live') {
          await this.fetchLiveMonitors();
        }
      }, 3000);
    },

    _applyColorScheme(storedValue) {
      const meta = document.querySelector('meta[name="color-scheme"]');
      if (meta) meta.content = storedValue || 'light dark';
      if (this.darkMode) {
        document.documentElement.classList.add('dark');
      } else {
        document.documentElement.classList.remove('dark');
      }
    },

    _installDialogLightDismissFallback() {
      if ('closedBy' in HTMLDialogElement.prototype) return;
      const outside = (e, dialog) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
        let pressedOutside = false;
        dialog.addEventListener('pointerdown', (e) => { pressedOutside = outside(e, dialog); });
        dialog.addEventListener('pointerup', (e) => {
          if (pressedOutside && outside(e, dialog)) dialog.close();
        });
      }
    },

    toggleSidebar() {
      if (window.innerWidth < 1280) {
        this.mobileMenuOpen = !this.mobileMenuOpen;
      } else {
        this.sidebarCollapsed = !this.sidebarCollapsed;
      }
    },

    toggleTheme() {
      const sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      const targetDark = !this.darkMode;
      this.darkMode = targetDark;
      try {
        if (targetDark === sysDark) {
          localStorage.removeItem('color-scheme');
          this._applyColorScheme(null);
        } else {
          const val = targetDark ? 'dark' : 'light';
          localStorage.setItem('color-scheme', val);
          this._applyColorScheme(val);
        }
      } catch (e) {
        this._applyColorScheme(targetDark ? 'dark' : 'light');
      }
      this.$nextTick(() => this.renderCharts());
    },

    navigate(tab) {
      this.activeTab = tab;
      this.mobileMenuOpen = false;
      if (tab === 'projects' || tab === 'overview') this.fetchProjects();
      if (tab === 'clips' || tab === 'overview') this.fetchAllClips();
      if (tab === 'settings') this.fetchSettings();
      if (tab === 'live') this.fetchLiveMonitors();
      if (tab === 'overview') {
        this.$nextTick(() => this.renderCharts());
      }
    },

    topViralityScore() {
      if (!this.allClips.length) return 0;
      return Math.max(...this.allClips.map(c => Number(c.virality_score) || 0));
    },

    avgViralityScore() {
      if (!this.allClips.length) return 0;
      const sum = this.allClips.reduce((acc, c) => acc + (Number(c.virality_score) || 0), 0);
      return Math.round(sum / this.allClips.length);
    },

    filteredClips() {
      let list = this.allClips;
      if (this.clipFilterProjectId) {
        list = list.filter(c => c.project_id === this.clipFilterProjectId);
      }
      if (this.searchQuery && this.searchQuery.trim()) {
        const q = this.searchQuery.trim().toLowerCase();
        list = list.filter(c =>
          (c.title || '').toLowerCase().includes(q) ||
          (c.hook_text || '').toLowerCase().includes(q) ||
          (c.category || '').toLowerCase().includes(q)
        );
      }
      return list;
    },

    filterByProject(projectId) {
      this.clipFilterProjectId = projectId;
      this.navigate('clips');
    },

    renderCharts() {
      if (typeof ApexCharts === 'undefined') return;
      const barEl = document.getElementById('viralityBarChart');
      const radEl = document.getElementById('viralityRadialChart');
      if (!barEl || !radEl) return;

      const textColor = this.darkMode ? '#98A2B3' : '#667085';
      const gridColor = this.darkMode ? '#1D2939' : '#F2F4F7';

      // 1. TailAdmin Bar Chart (Virality Score per Clip)
      const topClips = this.allClips.slice(0, 8);
      const barLabels = topClips.length ? topClips.map((c, i) => `Clip #${i + 1}`) : ['Clip #1', 'Clip #2', 'Clip #3'];
      const barValues = topClips.length ? topClips.map(c => Number(c.virality_score) || 0) : [0, 0, 0];

      if (this.barChart) this.barChart.destroy();
      this.barChart = new ApexCharts(barEl, {
        series: [{ name: 'Viral Score', data: barValues }],
        chart: {
          type: 'bar',
          height: 210,
          fontFamily: 'Outfit, sans-serif',
          toolbar: { show: false },
        },
        colors: ['#465FFF'],
        plotOptions: {
          bar: {
            horizontal: false,
            columnWidth: '38%',
            borderRadius: 6,
            borderRadiusApplication: 'end',
          },
        },
        dataLabels: { enabled: false },
        xaxis: {
          categories: barLabels,
          axisBorder: { show: false },
          axisTicks: { show: false },
          labels: { style: { colors: textColor, fontSize: '12px' } },
        },
        yaxis: {
          min: 0,
          max: 100,
          labels: { style: { colors: textColor, fontSize: '12px' } },
        },
        grid: {
          borderColor: gridColor,
          yaxis: { lines: { show: true } },
        },
        tooltip: {
          theme: this.darkMode ? 'dark' : 'light',
          y: { formatter: (v) => `${v} / 100` },
        },
      });
      this.barChart.render();

      // 2. TailAdmin Semi-Circle RadialBar Gauge (Average Virality Score)
      const avg = this.avgViralityScore();
      if (this.radialChart) this.radialChart.destroy();
      this.radialChart = new ApexCharts(radEl, {
        series: [avg],
        chart: {
          type: 'radialBar',
          height: 280,
          fontFamily: 'Outfit, sans-serif',
          sparkline: { enabled: true },
        },
        plotOptions: {
          radialBar: {
            startAngle: -85,
            endAngle: 85,
            hollow: { size: '76%' },
            track: {
              background: this.darkMode ? '#1D2939' : '#E4E7EC',
              strokeWidth: '100%',
              margin: 5,
            },
            dataLabels: {
              name: { show: false },
              value: {
                fontSize: '34px',
                fontWeight: '700',
                offsetY: -22,
                color: this.darkMode ? '#FFFFFF' : '#1D2939',
                formatter: (val) => `${val}%`,
              },
            },
          },
        },
        fill: { type: 'solid', colors: ['#465FFF'] },
        stroke: { lineCap: 'round' },
      });
      this.radialChart.render();
    },

    playHoverVideo(cardEl) {
      const vid = cardEl.querySelector('.hover-preview-video');
      if (vid) {
        vid.style.opacity = '1';
        vid.play().catch(() => {});
      }
    },

    stopHoverVideo(cardEl) {
      const vid = cardEl.querySelector('.hover-preview-video');
      if (vid) {
        vid.pause();
        vid.currentTime = 0;
        vid.style.opacity = '0';
      }
    },

    openClipStudio(clip) {
      const box = clip.facecam_box || { x: 0.72, y: 0.62, w: 0.26, h: 0.34 };
      this.studioForm = {
        id: clip.id || '',
        project_id: clip.project_id || '',
        title: clip.title || '',
        hook_text: clip.hook_text || '',
        caption: clip.caption || '',
        hashtags: clip.hashtags || '',
        reason: clip.reason || '',
        virality_score: Number(clip.virality_score) || 0,
        start_time: Number(clip.start_time) || 0,
        end_time: Number(clip.end_time) || 0,
        duration: Number(clip.duration) || 0,
        layout_mode: clip.layout_mode || 'gaming_split',
        subtitle_style: clip.subtitle_style || 'karaoke_yellow',
        video_url: clip.video_url || '',
        thumbnail_url: clip.thumbnail_url || '',
        updated_at: clip.updated_at || '',
        facecam_box: { x: box.x, y: box.y, w: box.w, h: box.h },
      };
      if (this.$refs.studioDialog && !this.$refs.studioDialog.open) {
        this.$refs.studioDialog.showModal();
      }
    },

    closeClipStudio() {
      if (this.$refs.studioDialog && this.$refs.studioDialog.open) {
        this.$refs.studioDialog.close();
      }
    },

    onStudioDialogClose() {
      const vid = this.$refs.studioDialog?.querySelector('video');
      if (vid) vid.pause();
    },

    openEditModalFromStudio() {
      const c = this.studioForm;
      this.closeClipStudio();
      this.editModal = {
        clipId: c.id,
        projectId: c.project_id,
        title: c.title,
        hook_text: c.hook_text,
        caption: c.caption,
        hashtags: c.hashtags,
        start_time: Number(c.start_time) || 0,
        end_time: Number(c.end_time) || 30,
        layout_mode: c.layout_mode || 'gaming_split',
        subtitle_style: c.subtitle_style || 'karaoke_yellow',
        facecam_box: { ...(c.facecam_box || { x: 0.72, y: 0.62, w: 0.26, h: 0.34 }) },
        dragging: false,
        dragStartX: 0,
        dragStartY: 0,
      };
      if (this.$refs.editDialog && !this.$refs.editDialog.open) {
        this.$refs.editDialog.showModal();
      }
    },

    closeEditModal() {
      if (this.$refs.editDialog && this.$refs.editDialog.open) {
        this.$refs.editDialog.close();
      }
    },

    activePreviewUrl() {
      const pid = this.editModal.projectId || this.studioForm.project_id;
      const proj = this.projects.find(p => p.id === pid);
      return proj?.preview_frame_url || '';
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

    async fetchAllClips() {
      try {
        const res = await fetch('/api/clips');
        const data = await res.json();
        this.allClips = (data.clips || []).sort((a, b) => (b.virality_score || 0) - (a.virality_score || 0));
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
        this.notify('Pengaturan berhasil disimpan!');
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
        if (data.info.suggested_layout) {
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
          clip_count: Number(this.form.clip_count) || 4,
          min_duration: Number(this.form.min_duration) || 25,
          max_duration: Number(this.form.max_duration) || 50,
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

        this.notify('Proyek dimulai! Pantau progresnya di halaman Source Projects.');
        this.form.youtube_url = '';
        this.inspectedInfo = null;
        await this.fetchProjects();
        this.navigate('projects');
      } catch (e) {
        this.notify(e.message, 'error');
      } finally {
        this.submitting = false;
      }
    },

    async startLiveWatcherFromForm() {
      this.liveForm.youtube_url = this.form.youtube_url.trim();
      this.liveForm.layout_mode = this.form.layout_mode;
      this.liveForm.facecam_preset = this.form.facecam_preset;
      this.liveForm.subtitle_style = this.form.subtitle_style;
      this.navigate('live');
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
        this.notify('Real-Time Live Monitor aktif!');
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

    async deleteProject(projectId) {
      if (!confirm('Hapus proyek beserta semua klip videonya?')) return;
      await fetch(`/api/projects/${projectId}`, { method: 'DELETE' });
      await Promise.all([this.fetchProjects(), this.fetchAllClips()]);
      this.notify('Proyek dihapus.');
    },

    async retryProject(projectId) {
      try {
        const res = await fetch(`/api/projects/${projectId}/retry`, { method: 'POST' });
        if (!res.ok) throw new Error('Gagal memulai ulang proyek');
        this.notify('Turbo Mode dimulai!');
        await this.fetchProjects();
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async reanalyzeProject(projectId) {
      try {
        const res = await fetch(`/api/projects/${projectId}/reanalyze`, { method: 'POST' });
        if (!res.ok) throw new Error('Gagal memulai analisis ulang');
        this.notify('AI Viral Director V2 sedang mencari ulang momen & Hook 0:00 terbaik...');
        await this.fetchProjects();
        this.navigate('projects');
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },

    async copyCaption(clip) {
      if (!clip) return;
      const fullText = `${clip.caption || clip.title}\n\n${clip.hashtags || ''}`.trim();
      try {
        await navigator.clipboard.writeText(fullText);
        this.notify('Caption + Hashtag berhasil disalin!');
      } catch (e) {
        this.notify('Gagal menyalin otomatis.', 'error');
      }
    },

    async publishClip(clip, platforms) {
      if (!clip || !clip.id) return;
      this.publishingId = clip.id;
      try {
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
        this.notify(messages.join(' | ') || 'Proses selesai', hasError ? 'error' : 'success');
        await this.fetchAllClips();
      } catch (e) {
        this.notify('Gagal menghubungi API posting: ' + e.message, 'error');
      } finally {
        this.publishingId = null;
      }
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
        this.closeEditModal();
        this.notify(rerender ? 'Sedang merender ulang video 9:16...' : 'Perubahan disimpan!');
        await this.fetchAllClips();
      } catch (e) {
        this.notify(e.message, 'error');
      }
    },
  };
}
