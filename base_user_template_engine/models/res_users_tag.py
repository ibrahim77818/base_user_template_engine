# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class ResUsersTag(models.Model):
    _name = "res.users.tag"
    _description = "User Tag"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    color = fields.Integer()
    active = fields.Boolean(default=True)
    description = fields.Text()
    user_ids = fields.Many2many(
        "res.users", "res_users_tag_user_rel", "tag_id", "user_id", string="Users")
    template_ids = fields.Many2many(
        "res.users.template", "res_users_template_tag_rel", "tag_id", "template_id",
        string="Templates")
    user_count = fields.Integer(compute="_compute_user_count")

    _sql_constraints = [
        ("name_uniq", "unique(name)", "A user tag with this name already exists."),
    ]

    @api.depends("user_ids")
    def _compute_user_count(self):
        for tag in self:
            tag.user_count = len(tag.user_ids)

    def write(self, vals):
        """Membership edited from the tag side must behave like editing user tags."""
        if "user_ids" not in vals or self.env.context.get("skip_user_template_engine"):
            return super().write(vals)
        before = self.user_ids
        res = super().write(vals)
        after = self.user_ids
        changed = (before - after) | (after - before)
        if changed:
            self.env["res.users.template"].sudo()._apply_to_users(
                changed, trigger="tag_change", auto_only=True)
        return res

    def action_view_users(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Users"),
            "res_model": "res.users",
            "view_mode": "tree,form",
            "domain": [("tag_ids", "in", self.ids)],
        }

    def action_open_apply_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply User Templates"),
            "res_model": "res.users.template.apply.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_target": "tags", "default_tag_ids": self.ids},
        }
