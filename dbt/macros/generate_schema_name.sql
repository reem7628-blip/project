{# Use the folder's schema name as is (staging, silver, gold, quality) instead of dbt's default "main_<name>". #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
