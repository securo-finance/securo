{{/*
Expand the name of the chart.
*/}}
{{- define "securo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "securo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "securo.labels" -}}
helm.sh/chart: {{ include "securo.chart" . }}
app.kubernetes.io/name: {{ include "securo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "securo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "securo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "securo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "securo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "securo.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret that holds the plugin runner token and the egress secret. Either the one
the chart creates or the user's existingSecret.
*/}}
{{- define "securo.pluginsSecretName" -}}
{{- if .Values.plugins.runner.existingSecret }}
{{- .Values.plugins.runner.existingSecret }}
{{- else }}
{{- printf "%s-plugins" (include "securo.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Image for the plugin runner and the egress proxy: the backend image unless the
user pointed them somewhere else.
*/}}
{{- define "securo.pluginsImage" -}}
{{- $repository := .Values.plugins.runner.image.repository | default .Values.backend.image.repository }}
{{- $tag := .Values.plugins.runner.image.tag | default .Values.backend.image.tag }}
{{- printf "%s:%s" $repository $tag }}
{{- end }}

{{/*
Stable checksum for the plugin tokens. Renders the same value for every caller
in one render (the Secret template itself uses random values on install), and
changes only when the live Secret data changes.
*/}}
{{- define "securo.pluginsSecretChecksum" -}}
{{- $secret := lookup "v1" "Secret" .Release.Namespace (include "securo.pluginsSecretName" .) }}
{{- $explicit := dict "token" .Values.plugins.runner.token "egress" .Values.plugins.runner.egressSecret }}
{{- toJson (dict "data" (default dict ($secret.data)) "explicit" (pick $explicit "token" "egress")) | sha256sum }}
{{- end }}

{{/*
`plugins` is a new top-level value. Under `helm upgrade --reuse-values` Helm
keeps the old value map, so the key can be absent; every condition guards it.
*/}}
{{- define "securo.pluginsRunnerEnabled" -}}
{{- $plugins := .Values.plugins | default dict }}
{{- $runner := get $plugins "runner" | default dict }}
{{- if get $runner "enabled" }}true{{ end }}
{{- end }}

{{/*
Compute the frontend URL based on global.domain and global.tls
*/}}
{{- define "securo.frontendUrl" -}}
{{- if .Values.global.tls -}}
https://{{ .Values.global.domain }}
{{- else -}}
http://{{ .Values.global.domain }}
{{- end -}}
{{- end -}}

{{/*
Compute the default OAuth Redirect URI
*/}}
{{- define "securo.oauthRedirectUri" -}}
{{ include "securo.frontendUrl" . }}/oauth/callback
{{- end -}}

{{/*
Name of the claim the attachment mounts use: the one the operator already has
when `persistence.attachments.existingClaim` is set, the chart's own otherwise.
*/}}
{{- define "securo.attachmentsClaimName" -}}
{{- if .Values.persistence.attachments.existingClaim -}}
{{- .Values.persistence.attachments.existingClaim | quote -}}
{{- else -}}
{{- printf "%s-attachments" (include "securo.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Init container that holds a pod until the database is at the head revision of
the image it runs, the same check Compose makes before each service starts.
The migration Job brings the database there; until it does, new pods stay in
Init and, on an upgrade, the old ones keep serving. Takes a dict: `root` (the
chart's `.`) and `component` (the component's values, for its pull policy,
resources and extras).
*/}}
{{- define "securo.waitForSchema" -}}
{{- $root := .root -}}
{{- $component := .component -}}
- name: wait-for-schema
  image: "{{ $root.Values.backend.image.repository }}:{{ $root.Values.backend.image.tag }}"
  imagePullPolicy: {{ $component.image.pullPolicy }}
  command:
    - sh
    - -c
    - |
      until alembic current --check-heads; do
        echo "The database is not at this image's migration head yet; waiting for the migration Job..."
        sleep 5
      done
  envFrom:
    - configMapRef:
        name: {{ include "securo.fullname" $root }}-config
    - secretRef:
        {{- if $root.Values.global.existingSecret }}
        name: {{ $root.Values.global.existingSecret }}
        {{- else }}
        name: {{ include "securo.fullname" $root }}-secrets
        {{- end }}
    {{- with $component.extraEnvFrom }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  {{- with $component.extraEnv }}
  env:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $component.extraVolumeMounts }}
  volumeMounts:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $component.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}
