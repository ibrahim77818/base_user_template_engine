# -*- coding: utf-8 -*-
"""User Access & Preference Template engine.

Resolution model
----------------
1. *Applicable templates* for a user = active templates that are
   - ``scope == 'global'`` (all internal users, optionally portal), or
   - ``scope == 'targeted'`` and the user is listed explicitly or carries one
     of the template's tags,
   and whose owner ``company_id`` (if any) is among the user's companies.
2. Templates are applied in ascending ``(sequence, id)`` order, so a higher
   sequence means a *higher precedence* for override-type attributes.
3. For each template, every attribute is resolved through its *policy*:
   ``skip`` (not managed), ``own`` (value on this template) or ``inherit``
   (walk up ``parent_id`` until a template with ``own`` or ``skip``).
   The *mode* (merge / override) always comes from the applied template,
   never from the parent that supplied the value.
4. Merge / override semantics:
   - scalar attributes: override -> always set; merge -> fill only if empty.
   - multi attributes: override -> replace; merge -> union.
   - revocations: always subtracted.
5. Post-processing: implied groups closure, default company forced into the
   allowed companies, user-type groups (Internal / Portal / Public) are
   never added, removed or replaced by a template.
6. All changes for one user are computed in memory first ("plan"), then
   users with identical plans are written in a single batched ``write``.
"""
from collections import OrderedDict, defaultdict

from markupsafe import Markup, escape

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import ValidationError
from odoo.fields import Command

SKIP_CTX = "skip_user_template_engine"

POLICY_SELECTION = [
    ("skip", "Not Managed"),
    ("own", "Set on this Template"),
    ("inherit", "Inherit from Parent"),
]
MODE_SELECTION = [
    ("default", "Template Default"),
    ("merge", "Merge (Additive)"),
    ("override", "Override (Strict)"),
]

# Ordered registry of managed attributes. The order matters:
#   - companies before default company (consistency fix-up)
#   - groups before revocations (a template may grant then revoke)
TEMPLATE_ATTRIBUTES = OrderedDict([
    ("lang", {"kind": "scalar", "user_field": "lang", "label": "Language"}),
    ("tz", {"kind": "scalar", "user_field": "tz", "label": "Timezone"}),
    ("home_action_id", {"kind": "scalar", "user_field": "action_id", "label": "Home Action"}),
    ("allowed_company_ids", {"kind": "multi", "user_field": "company_ids", "label": "Allowed Companies"}),
    ("default_company_id", {"kind": "scalar", "user_field": "company_id", "label": "Default Company"}),
    ("group_ids", {"kind": "multi", "user_field": "groups_id", "label": "Access Groups",
                   "groups": True}),
    ("group_remove_ids", {"kind": "remove", "target": "group_ids", "label": "Revoked Groups",
                          "groups": True}),
])
STATE_ATTRIBUTES = [a for a, m in TEMPLATE_ATTRIBUTES.items() if m["kind"] != "remove"]


class ResUsersTemplate(models.Model):
    _name = "res.users.template"
    _description = "User Access & Preference Template"
    _order = "sequence, id"

    # ------------------------------------------------------------------
    # Identity / behaviour
    # ------------------------------------------------------------------
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(
        default=10,
        help="Application order. Templates are applied from the lowest to the "
             "highest sequence, so higher sequence = higher precedence for "
             "override attributes.")
    color = fields.Integer()
    description = fields.Text()
    company_id = fields.Many2one(
        "res.company", string="Owner Company",
        help="When set, the template only applies to users allowed in this company "
             "and is only visible inside it.")

    scope = fields.Selection(
        [("global", "Global (all internal users)"), ("targeted", "Targeted (tags / users)")],
        default="targeted", required=True)
    include_portal = fields.Boolean(
        help="Global templates also apply to portal users (preferences only: "
             "access groups are never applied to share users).")
    tag_ids = fields.Many2many(
        "res.users.tag", "res_users_template_tag_rel", "template_id", "tag_id",
        string="User Tags")
    user_ids = fields.Many2many(
        "res.users", "res_users_template_user_rel", "template_id", "user_id",
        string="Explicit Users", domain=[("share", "=", False)])

    apply_mode = fields.Selection(
        [("merge", "Merge (Additive)"), ("override", "Override (Strict)")],
        default="merge", required=True,
        help="Default mode for all attributes set to 'Template Default'.\n"
             "Merge: lists are united, single values only fill empty fields.\n"
             "Override: lists are replaced, single values are always written.")
    parent_id = fields.Many2one("res.users.template", string="Parent Template",
                                ondelete="restrict", index=True)
    child_ids = fields.One2many("res.users.template", "parent_id", string="Child Templates")
    auto_apply = fields.Boolean(
        default=True,
        help="Apply automatically when a user is created or when the user's tags change.")
    protect_admins = fields.Boolean(
        default=True,
        help="Never modify the access groups of users with the Settings (Administration) right.")

    # ------------------------------------------------------------------
    # Managed attributes (value + policy + mode)
    # ------------------------------------------------------------------
    lang = fields.Selection(selection="_get_lang_selection", string="Language")
    lang_policy = fields.Selection(POLICY_SELECTION, string="Language Policy", default="skip", required=True)
    lang_mode = fields.Selection(MODE_SELECTION, string="Language Mode", default="default", required=True)

    tz = fields.Selection(_tz_get, string="Timezone")
    tz_policy = fields.Selection(POLICY_SELECTION, string="Timezone Policy", default="skip", required=True)
    tz_mode = fields.Selection(MODE_SELECTION, string="Timezone Mode", default="default", required=True)

    home_action_id = fields.Many2one("ir.actions.actions", string="Home Action")
    home_action_id_policy = fields.Selection(POLICY_SELECTION, string="Home Action Policy", default="skip", required=True)
    home_action_id_mode = fields.Selection(MODE_SELECTION, string="Home Action Mode", default="default", required=True)

    allowed_company_ids = fields.Many2many(
        "res.company", "res_users_template_company_rel", "template_id", "company_id",
        string="Allowed Companies")
    allowed_company_ids_policy = fields.Selection(POLICY_SELECTION, string="Allowed Companies Policy", default="skip", required=True)
    allowed_company_ids_mode = fields.Selection(MODE_SELECTION, string="Allowed Companies Mode", default="default", required=True)

    default_company_id = fields.Many2one("res.company", string="Default Company")
    default_company_id_policy = fields.Selection(POLICY_SELECTION, string="Default Company Policy", default="skip", required=True)
    default_company_id_mode = fields.Selection(MODE_SELECTION, string="Default Company Mode", default="default", required=True)

    group_ids = fields.Many2many(
        "res.groups", "res_users_template_group_rel", "template_id", "group_id",
        string="Access Groups", domain=lambda self: self._get_assignable_groups_domain())
    group_ids_policy = fields.Selection(POLICY_SELECTION, string="Granted Groups Policy", default="skip", required=True)
    group_ids_mode = fields.Selection(MODE_SELECTION, string="Granted Groups Mode", default="default", required=True)

    group_remove_ids = fields.Many2many(
        "res.groups", "res_users_template_group_remove_rel", "template_id", "group_id",
        string="Revoked Groups", domain=lambda self: self._get_assignable_groups_domain(),
        help="Groups removed from the user after all grants of this template. "
             "A group still implied by another kept group stays (standard Odoo behaviour).")
    group_remove_ids_policy = fields.Selection(POLICY_SELECTION, string="Revoked Groups Policy", default="skip", required=True)

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------
    audience_count = fields.Integer(compute="_compute_audience_count", string="Targeted Users")
    child_count = fields.Integer(compute="_compute_child_count")
    effective_summary_html = fields.Html(
        compute="_compute_effective_summary_html", sanitize=False,
        string="Effective Configuration")

    @api.model
    def _get_lang_selection(self):
        return self.env["res.lang"].get_installed()

    @api.model
    def _get_user_type_groups(self):
        category = self.env.ref("base.module_category_user_type", raise_if_not_found=False)
        if not category:
            return self.env["res.groups"].sudo().browse()
        return self.env["res.groups"].sudo().search([("category_id", "=", category.id)])

    @api.model
    def _get_assignable_groups_domain(self):
        return [("id", "not in", self._get_user_type_groups().ids)]

    def _compute_audience_count(self):
        for tmpl in self:
            tmpl.audience_count = len(tmpl._get_audience_users()) if tmpl.id else 0

    @api.depends("child_ids")
    def _compute_child_count(self):
        for tmpl in self:
            tmpl.child_count = len(tmpl.child_ids)

    def _compute_effective_summary_html(self):
        selections = {
            "lang": dict(self._get_lang_selection()),
            "tz": dict(_tz_get(self)),
        }
        for tmpl in self:
            if not tmpl.id:
                tmpl.effective_summary_html = False
                continue
            try:
                effective = tmpl._get_effective_values()
            except ValidationError as exc:
                tmpl.effective_summary_html = Markup('<div class="alert alert-danger">%s</div>') % exc.args[0]
                continue
            rows = Markup()
            for attr, meta in TEMPLATE_ATTRIBUTES.items():
                spec = effective.get(attr)
                if not spec:
                    value, mode, source = _("Not managed"), "-", "-"
                else:
                    value = self._format_value(attr, spec["value"], selections)
                    mode = dict(MODE_SELECTION).get(spec["mode"], _("Subtract")) if spec["mode"] else _("Subtract")
                    source = spec["source"].display_name
                rows += Markup("<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td><td>%s</td></tr>") % (
                    meta["label"], value or "-", mode, source)
            tmpl.effective_summary_html = Markup(
                '<table class="table table-sm table-striped"><thead><tr>'
                "<th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>"
            ) % (_("Attribute"), _("Resolved Value"), _("Mode"), _("Value Source"), rows)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("parent_id")
    def _check_parent_cycle(self):
        for tmpl in self:
            seen, current = set(), tmpl
            while current:
                if current.id in seen:
                    raise ValidationError(_("Template inheritance cannot be recursive (%s).", tmpl.name))
                seen.add(current.id)
                current = current.parent_id

    @api.constrains(*[f"{a}_policy" for a in TEMPLATE_ATTRIBUTES], "parent_id")
    def _check_inherit_needs_parent(self):
        for tmpl in self.filtered(lambda t: not t.parent_id):
            inherited = [m["label"] for a, m in TEMPLATE_ATTRIBUTES.items()
                         if tmpl[f"{a}_policy"] == "inherit"]
            if inherited:
                raise ValidationError(_(
                    "Template '%(name)s' inherits %(attrs)s but has no parent template.",
                    name=tmpl.name, attrs=", ".join(inherited)))

    @api.constrains("group_ids", "group_remove_ids")
    def _check_no_user_type_groups(self):
        user_types = self._get_user_type_groups()
        for tmpl in self:
            if (tmpl.group_ids | tmpl.group_remove_ids) & user_types:
                raise ValidationError(_(
                    "User type groups (Internal User / Portal / Public) cannot be managed by templates."))

    @api.constrains("allowed_company_ids", "default_company_id",
                    "allowed_company_ids_policy", "default_company_id_policy",
                    "allowed_company_ids_mode", "default_company_id_mode")
    def _check_company_consistency(self):
        for tmpl in self:
            if (tmpl.allowed_company_ids_policy == "own" and tmpl.default_company_id_policy == "own"
                    and tmpl._resolve_mode("allowed_company_ids") == "override"
                    and tmpl.default_company_id
                    and tmpl.default_company_id not in tmpl.allowed_company_ids):
                raise ValidationError(_(
                    "Template '%s': the default company must be one of the allowed companies "
                    "when allowed companies are overridden.", tmpl.name))

    # ------------------------------------------------------------------
    # Resolution (inheritance)
    # ------------------------------------------------------------------
    def _resolve_mode(self, attr):
        self.ensure_one()
        mode = self[f"{attr}_mode"]
        return self.apply_mode if mode == "default" else mode

    def _resolve_attribute_source(self, attr):
        """Return the template that supplies the value of ``attr`` (or empty)."""
        self.ensure_one()
        seen, current = set(), self
        while current:
            if current.id in seen:
                raise ValidationError(_("Recursive template inheritance on '%s'.", self.name))
            seen.add(current.id)
            policy = current[f"{attr}_policy"]
            if policy == "own":
                return current
            if policy == "skip":
                return self.browse()
            current = current.parent_id
        return self.browse()

    def _get_own_value(self, attr):
        self.ensure_one()
        value = self[attr]
        if isinstance(value, models.BaseModel):
            return value.id if TEMPLATE_ATTRIBUTES[attr]["kind"] == "scalar" else value.ids
        return value or False

    def _get_effective_values(self):
        """Fully resolved configuration of this template.

        :return: ``{attr: {'value', 'mode', 'source'}}`` for managed attributes only.
        """
        self.ensure_one()
        result = {}
        for attr, meta in TEMPLATE_ATTRIBUTES.items():
            source = self._resolve_attribute_source(attr)
            if not source:
                continue
            result[attr] = {
                "value": source._get_own_value(attr),
                "mode": None if meta["kind"] == "remove" else self._resolve_mode(attr),
                "source": source,
            }
        return result

    # ------------------------------------------------------------------
    # Targeting
    # ------------------------------------------------------------------
    def _is_applicable_to(self, user):
        self.ensure_one()
        if self.company_id and self.company_id not in user.company_ids:
            return False
        if self.scope == "global":
            return not user.share or self.include_portal
        return user in self.user_ids or bool(self.tag_ids & user.tag_ids)

    def _get_audience_users(self):
        Users = self.env["res.users"].sudo()
        result = Users.browse()
        for tmpl in self.sudo():
            if tmpl.scope == "global":
                domain = [("id", "!=", SUPERUSER_ID)]
                if not tmpl.include_portal:
                    domain.append(("share", "=", False))
                users = Users.search(domain)
            else:
                users = (tmpl.user_ids | tmpl.tag_ids.user_ids).filtered("active")
            if tmpl.company_id:
                users = users.filtered(lambda u, c=tmpl.company_id: c in u.company_ids)
            result |= users
        return result

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------
    @api.model
    def _read_user_state(self, user):
        state = {}
        for attr in STATE_ATTRIBUTES:
            meta = TEMPLATE_ATTRIBUTES[attr]
            value = user[meta["user_field"]]
            if meta["kind"] == "multi":
                state[attr] = set(value.ids)
            elif isinstance(value, models.BaseModel):
                state[attr] = value.id
            else:
                state[attr] = value or False
        return state

    @api.model
    def _close_groups(self, group_ids):
        groups = self.env["res.groups"].sudo().browse(group_ids)
        return set((groups | groups.trans_implied_ids).ids)

    def _compute_user_plan(self, user, templates, effective_cache):
        """Simulate the ordered application of ``templates`` on ``user``.

        Pure function: nothing is written. Returns a plan dict.
        """
        initial = self._read_user_state(user)
        state = {k: (set(v) if isinstance(v, set) else v) for k, v in initial.items()}
        is_admin = user._is_system()
        user_type_ids = set(user.groups_id.ids) & set(self._get_user_type_groups().ids)
        company_explicit = False
        applied = self.browse()

        for tmpl in templates:
            touched = False
            for attr, spec in effective_cache[tmpl.id].items():
                meta = TEMPLATE_ATTRIBUTES[attr]
                if meta.get("groups") and (user.share or (tmpl.protect_admins and is_admin)):
                    continue
                kind, value, mode = meta["kind"], spec["value"], spec["mode"]
                if kind == "scalar":
                    if not value or (mode != "override" and state[attr]):
                        continue
                    if attr == "default_company_id":
                        # An explicitly enforced default company must stay allowed.
                        company_explicit = True
                    if state[attr] != value:
                        state[attr] = value
                        touched = True
                elif kind == "multi":
                    value = set(value)
                    if mode == "override":
                        if attr == "allowed_company_ids" and not value:
                            continue  # never leave a user without any company
                        if attr == "group_ids":
                            value |= user_type_ids
                        state[attr] = value
                    else:
                        state[attr] |= value
                    touched = True
                else:  # remove
                    state[meta["target"]] -= set(value) - user_type_ids
                    touched = True
            if touched:
                applied |= tmpl

        # -- post-processing ------------------------------------------------
        state["group_ids"] = self._close_groups(state["group_ids"])
        if state["default_company_id"] not in state["allowed_company_ids"]:
            if company_explicit or not state["allowed_company_ids"]:
                state["allowed_company_ids"].add(state["default_company_id"])
            else:
                companies = self.env["res.company"].sudo().browse(state["allowed_company_ids"]).sorted()
                state["default_company_id"] = companies[:1].id

        vals = {}
        for attr in STATE_ATTRIBUTES:
            if state[attr] == initial[attr]:
                continue
            meta = TEMPLATE_ATTRIBUTES[attr]
            if meta["kind"] == "multi":
                vals[meta["user_field"]] = [Command.set(sorted(state[attr]))]
            else:
                vals[meta["user_field"]] = state[attr]
        return {"user": user, "vals": vals, "initial": initial, "final": state, "templates": applied}

    @api.model
    def _get_templates_for_users(self, users, templates=None, auto_only=False):
        """Map user id -> ordered recordset of templates to apply."""
        if templates is not None:
            ordered = templates.sudo().filtered("active").sorted(lambda t: (t.sequence, t.id))
            return {user.id: ordered for user in users}
        domain = [("auto_apply", "=", True)] if auto_only else []
        candidates = self.sudo().search(domain, order="sequence, id")
        return {user.id: candidates.filtered(lambda t, u=user: t._is_applicable_to(u)) for user in users}

    @api.model
    def _apply_to_users(self, users, templates=None, trigger="manual", dry_run=False, auto_only=False):
        """Main entry point of the engine.

        :param users: res.users recordset
        :param templates: explicit templates (applied in precedence order, targeting
                          ignored) or None to resolve applicable templates per user
        :param trigger: label stored in the application log
        :param dry_run: compute plans only, do not write
        :param auto_only: only consider templates flagged ``auto_apply``
        :return: ``{user_id: plan}`` for users that change
        """
        # Always work on real database records: inside an onchange (e.g. the wizard
        # preview) users reached through edited relations are temporary records whose
        # relational values (tags, companies) are temporary too and would not match.
        users = self.env["res.users"].sudo().browse(
            [uid for uid in users._origin.ids if uid and uid != SUPERUSER_ID])
        if templates is not None:
            templates = self.sudo().browse(templates._origin.ids)
        if not users:
            return {}
        mapping = self._get_templates_for_users(users, templates, auto_only)
        effective_cache = {}
        plans = {}
        for user in users:
            user_templates = mapping[user.id]
            if not user_templates:
                continue
            for tmpl in user_templates:
                if tmpl.id not in effective_cache:
                    effective_cache[tmpl.id] = tmpl._get_effective_values()
            plan = self._compute_user_plan(user, user_templates, effective_cache)
            if plan["vals"]:
                plans[user.id] = plan
        if dry_run or not plans:
            return plans

        # Batch identical writes together (one SQL round-trip per distinct plan).
        buckets = defaultdict(list)
        for user_id, plan in plans.items():
            key = repr(sorted((k, repr(v)) for k, v in plan["vals"].items()))
            buckets[key].append(user_id)
        Users = self.env["res.users"].sudo().with_context(**{SKIP_CTX: True})
        for user_ids in buckets.values():
            Users.browse(user_ids).write(plans[user_ids[0]]["vals"])

        self.env["res.users.template.log"].sudo().create([{
            "user_id": user_id,
            "template_ids": [Command.set(plan["templates"].ids)],
            "trigger": trigger,
            "changes": "\n".join(f"{label}: {before} → {after}"
                                 for label, before, after in self._describe_plan(plan)),
        } for user_id, plan in plans.items()])
        return plans

    # ------------------------------------------------------------------
    # Presentation helpers
    # ------------------------------------------------------------------
    @api.model
    def _format_value(self, attr, value, selections=None):
        meta = TEMPLATE_ATTRIBUTES[attr]
        selections = selections or {}
        if attr in ("lang", "tz"):
            return selections.get(attr, {}).get(value, value) or ""
        if not value:
            return ""
        comodel = self._fields[attr].comodel_name
        records = self.env[comodel].sudo().browse(value if meta["kind"] != "scalar" else [value])
        return ", ".join(records.mapped("display_name"))

    @api.model
    def _describe_plan(self, plan):
        """Human readable list of ``(label, before, after)`` tuples."""
        selections = {"lang": dict(self._get_lang_selection()), "tz": dict(_tz_get(self))}
        lines = []
        for attr in STATE_ATTRIBUTES:
            before, after = plan["initial"][attr], plan["final"][attr]
            if before == after:
                continue
            label = TEMPLATE_ATTRIBUTES[attr]["label"]
            if TEMPLATE_ATTRIBUTES[attr]["kind"] == "multi":
                added = self._format_value(attr, sorted(after - before))
                removed = self._format_value(attr, sorted(before - after))
                lines.append((label, f"-[{removed}]" if removed else "", f"+[{added}]" if added else ""))
            else:
                lines.append((label, self._format_value(attr, before, selections),
                              self._format_value(attr, after, selections)))
        return lines

    @api.model
    def _plans_to_html(self, plans, limit_note=None):
        if not plans:
            return Markup('<div class="alert alert-info">%s</div>') % _(
                "No changes: all targeted users already match their templates.")
        rows = Markup()
        for plan in plans.values():
            for label, before, after in self._describe_plan(plan):
                rows += Markup("<tr><td>%s</td><td>%s</td><td class='text-danger'>%s</td>"
                               "<td class='text-success'>%s</td><td>%s</td></tr>") % (
                    plan["user"].display_name, label, before, after,
                    ", ".join(plan["templates"].mapped("name")))
        note = Markup('<p class="text-muted">%s</p>') % limit_note if limit_note else Markup()
        return note + Markup(
            '<table class="table table-sm table-bordered"><thead><tr>'
            "<th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>"
        ) % (_("User"), _("Attribute"), _("Before"), _("After"), _("Templates"), rows)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_open_apply_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Apply User Templates"),
            "res_model": "res.users.template.apply.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_target": "audience",
                "default_strategy": "selected",
                "default_template_ids": self.ids,
            },
        }

    def action_view_audience(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Targeted Users"),
            "res_model": "res.users",
            "view_mode": "tree,form",
            "domain": [("id", "in", self._get_audience_users().ids)],
        }

    def action_view_children(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Child Templates"),
            "res_model": "res.users.template",
            "view_mode": "tree,form",
            "domain": [("parent_id", "=", self.id)],
            "context": {"default_parent_id": self.id},
        }

    @api.model
    def _cron_apply_all(self):
        users = self.env["res.users"].sudo().search([("id", "!=", SUPERUSER_ID)])
        self._apply_to_users(users, trigger="cron")
        return True
