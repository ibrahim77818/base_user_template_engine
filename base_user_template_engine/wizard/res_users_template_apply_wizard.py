# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

PREVIEW_LIMIT = 50


class ResUsersTemplateApplyWizard(models.TransientModel):
    _name = "res.users.template.apply.wizard"
    _description = "Bulk Apply User Templates"

    target = fields.Selection([
        ("users", "Selected Users"),
        ("tags", "Users with Tags"),
        ("audience", "Audience of Selected Templates"),
        ("all", "All Active Users"),
    ], default="users", required=True)
    user_ids = fields.Many2many(
        "res.users", "res_users_template_wiz_user_rel", "wizard_id", "user_id", string="Users")
    tag_ids = fields.Many2many(
        "res.users.tag", "res_users_template_wiz_tag_rel", "wizard_id", "tag_id", string="Tags")
    strategy = fields.Selection([
        ("resolve", "Resolve all applicable templates (recommended)"),
        ("selected", "Apply only the selected templates"),
    ], default="resolve", required=True,
        help="Resolve: each user receives the full stack of templates targeting them, "
             "in precedence order.\nSelected: only the chosen templates are applied, "
             "in sequence order, regardless of their targeting.")
    template_ids = fields.Many2many(
        "res.users.template", "res_users_template_wiz_tmpl_rel", "wizard_id", "template_id",
        string="Templates")
    target_count = fields.Integer(compute="_compute_preview")
    preview_html = fields.Html(compute="_compute_preview", sanitize=False, string="Preview")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        model, ids = self.env.context.get("active_model"), self.env.context.get("active_ids")
        if ids and model == "res.users" and "user_ids" in fields_list:
            res.update(target="users", user_ids=[(6, 0, ids)])
        elif ids and model == "res.users.tag" and "tag_ids" in fields_list:
            res.update(target="tags", tag_ids=[(6, 0, ids)])
        elif ids and model == "res.users.template" and "template_ids" in fields_list:
            res.update(target="audience", strategy="selected", template_ids=[(6, 0, ids)])
        return res

    def _get_target_users(self):
        self.ensure_one()
        Users = self.env["res.users"].sudo()
        if self.target == "users":
            users = self.user_ids._origin
        elif self.target == "tags":
            users = Users.search([("tag_ids", "in", self.tag_ids._origin.ids)])
        elif self.target == "audience":
            users = self.template_ids._origin._get_audience_users()
        else:
            users = Users.search([("id", "!=", SUPERUSER_ID)])
        return Users.browse(users.ids).filtered(lambda u: u.active and u.id != SUPERUSER_ID)

    def _get_templates_arg(self):
        return self.template_ids._origin if self.strategy == "selected" else None

    @api.depends("target", "user_ids", "tag_ids", "strategy", "template_ids")
    def _compute_preview(self):
        Template = self.env["res.users.template"]
        for wiz in self:
            users = wiz._get_target_users()
            wiz.target_count = len(users)
            if wiz.strategy == "selected" and not wiz.template_ids:
                wiz.preview_html = False
                continue
            note = None
            if len(users) > PREVIEW_LIMIT:
                note = _("Preview limited to the first %s of %s users.", PREVIEW_LIMIT, len(users))
                users = users[:PREVIEW_LIMIT]
            plans = Template._apply_to_users(users, wiz._get_templates_arg(), dry_run=True)
            wiz.preview_html = Template._plans_to_html(plans, note)

    def action_apply(self):
        self.ensure_one()
        if self.strategy == "selected" and not self.template_ids:
            raise UserError(_("Select at least one template."))
        users = self._get_target_users()
        if not users:
            raise UserError(_("No users match the selected target."))
        plans = self.env["res.users.template"]._apply_to_users(
            users, self._get_templates_arg(), trigger="wizard")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%(changed)s of %(total)s user(s) updated.",
                             changed=len(plans), total=len(users)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
