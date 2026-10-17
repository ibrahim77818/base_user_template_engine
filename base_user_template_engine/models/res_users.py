# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .res_users_template import SKIP_CTX

MANAGER_GROUP = "base_user_template_engine.group_user_template_manager"


class ResUsers(models.Model):
    _inherit = "res.users"

    tag_ids = fields.Many2many(
        "res.users.tag", "res_users_tag_user_rel", "user_id", "tag_id",
        string="User Tags", groups=MANAGER_GROUP)
    applicable_template_ids = fields.Many2many(
        "res.users.template", compute="_compute_applicable_template_ids",
        string="Applicable Templates", groups=MANAGER_GROUP)
    template_log_ids = fields.One2many(
        "res.users.template.log", "user_id", string="Template History", groups=MANAGER_GROUP)

    @api.depends("tag_ids", "share", "company_ids")
    def _compute_applicable_template_ids(self):
        Template = self.env["res.users.template"]
        mapping = Template._get_templates_for_users(self.filtered("id"))
        for user in self:
            user.applicable_template_ids = mapping.get(user.id, Template.browse())

    # ------------------------------------------------------------------
    # ORM overrides: automatic application
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if not self.env.context.get(SKIP_CTX):
            self.env["res.users.template"].sudo()._apply_to_users(
                users, trigger="create", auto_only=True)
        return users

    def write(self, vals):
        res = super().write(vals)
        if "tag_ids" in vals and not self.env.context.get(SKIP_CTX):
            self.env["res.users.template"].sudo()._apply_to_users(
                self, trigger="tag_change", auto_only=True)
        return res

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_apply_user_templates(self):
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only User Template Managers can apply templates."))
        plans = self.env["res.users.template"]._apply_to_users(self, trigger="manual")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if plans else "info",
                "message": _("%s user(s) updated.", len(plans)) if plans
                else _("Users already match their templates."),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
