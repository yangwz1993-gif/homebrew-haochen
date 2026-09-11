"""One configuration-to-runtime handoff, shared by onboarding and settings.

Saved credentials are not readiness. Wait for ongoing work, reload the process
environment when needed, restore the session, await model selection, then verify
the engine's actual state. No prompt may cross this boundary before success.
"""

import logging

from PyQt6.QtCore import QObject, QTimer

log = logging.getLogger("haochen.config_activation")


class ConfigActivation(QObject):
    def __init__(self, supervisor):
        super().__init__(supervisor)
        self.supervisor = supervisor
        self.client = supervisor.client
        self._target = None
        self._done = None
        self._phase = "idle"
        self._request = None
        self._ready_target = None
        self._reload = True
        self._idle = QTimer(self)
        self._idle.setInterval(50)
        self._idle.timeout.connect(self._when_idle)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(45_000)
        self._deadline.timeout.connect(self._failed)
        self.client.response.connect(self._response)
        supervisor.restarted.connect(self._restarted)
        supervisor.restart_failed.connect(self._failed)
        supervisor.restarting.connect(self._invalidate)
        supervisor.crashed.connect(self._invalidate)

    def _invalidate(self, *_args):
        self._ready_target = None

    def ensure_ready(self, provider, model, done):
        if (self._phase == "idle" and self._ready_target == (provider, model)
                and self.client.alive and not self.client.configuration_blocked):
            done(True, "模型已就绪")
            return
        self.apply(provider, model, done)

    def apply(self, provider, model, done, *, reload=True):
        if self._phase != "idle":
            done(False, "另一个配置正在生效，请稍后重试。")
            return
        self._target = (provider, model)
        self._done = done
        self._reload = reload
        log.info("applying configuration provider=%s model=%s reload=%s", provider, model, reload)
        self._ready_target = None
        self.client.configuration_blocked = True
        self._phase = "waiting"
        # Never interrupt a reply or tool execution to change credentials.
        self._idle.start()
        self._when_idle()

    def _when_idle(self):
        if any(ctrl.busy for ctrl in self.supervisor._ctrls):
            return
        self._idle.stop()
        self._deadline.start()
        if self._reload or not self.client.alive or self.supervisor._restarting:
            self._phase = "restarting"
            self.supervisor.restart_now(reason="configuration")
        else:
            self._select()

    def _restarted(self):
        if self._phase == "restarting":
            self._select()

    def _select(self):
        log.info("engine loaded; selecting configured model")
        self._phase = "selecting"
        try:
            self._request = self.client.set_model(*self._target)
        except RuntimeError:
            self._failed()

    def _response(self, response):
        if not self._request or response.get("id") != self._request:
            return
        self._request = None
        if not response.get("success"):
            log.warning("configuration RPC failed phase=%s code=%s", self._phase, response.get("errorCode", "rejected"))
            self._failed()
            return
        if self._phase == "selecting":
            self._phase = "checking"
            try:
                self._request = self.client.get_state()
            except RuntimeError:
                self._failed()
        elif self._phase == "checking":
            state = response.get("data") or {}
            model = state.get("model") or {}
            if (not isinstance(model, dict)
                    or (model.get("provider"), model.get("id")) != self._target):
                self._failed()
                return
            self._ready_target = self._target
            self.client.configuration_blocked = False
            self._finish(True, "模型已就绪，可以开始对话。")

    def _failed(self):
        if self._phase != "idle":
            self._finish(False, "配置已保存，但模型尚未就绪。请点“连接模型”重试；无需重新填写 Key。")

    def _finish(self, ok, message):
        log.info("configuration activation finished ready=%s phase=%s", ok, self._phase)
        self._idle.stop()
        self._deadline.stop()
        self._request = None
        self._phase = "idle"
        done, self._done = self._done, None
        if done:
            done(ok, message)

    def stop(self):
        self._idle.stop()
        self._deadline.stop()
        self._done = None
        self._request = None
        self._phase = "idle"
        self._ready_target = None
