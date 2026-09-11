{#
  One row per calendar day, 1960-01-01 to 2035-01-01 -- 1960 is wide
  enough to cover every ticker yfinance serves for this project; 2035 is
  intentionally *beyond* today; fct_security_price_projection needs real
  future calendar days to project onto, so this conformed date dimension
  covers them like any other date rather than stopping at today. `is_future`
  distinguishes them for anything that only wants realized history.
  `date_key` is a yyyymmdd integer so the facts can carry it without a
  join to compute.
#}

{%- set start_date = "cast('1960-01-01' as date)" -%}
{%- set end_date = "cast('2035-01-01' as date)" -%}

with spine as (
    {{ dbt_utils.date_spine(datepart="day", start_date=start_date, end_date=end_date) }}
),

days as (
    select
        cast(date_day as date) as date_day,
        -- days since 1970-01-01 (a Thursday). Guard the modulo so it is
        -- non-negative for pre-1970 dates too (SQL % keeps the sign of the
        -- dividend). ISO weekday: 1 = Monday .. 7 = Sunday.
        (
            (
                ((({{ dbt.datediff("cast('1970-01-01' as date)", "date_day", "day") }}) + 3) % 7)
                + 7
            ) % 7
        ) + 1 as iso_day_of_week
    from spine
),

final as (
    select
        cast(
            extract(year  from date_day) * 10000
          + extract(month from date_day) * 100
          + extract(day   from date_day)
            as integer
        )                                                     as date_key,
        date_day,
        extract(year    from date_day)                        as year_number,
        extract(quarter from date_day)                        as quarter_number,
        extract(month   from date_day)                        as month_number,
        case extract(month from date_day)
            when 1 then 'January'   when 2  then 'February' when 3  then 'March'
            when 4 then 'April'     when 5  then 'May'      when 6  then 'June'
            when 7 then 'July'      when 8  then 'August'   when 9  then 'September'
            when 10 then 'October'  when 11 then 'November' when 12 then 'December'
        end                                                   as month_name,
        extract(day from date_day)                            as day_of_month,
        iso_day_of_week,
        case iso_day_of_week
            when 1 then 'Monday'    when 2 then 'Tuesday'  when 3 then 'Wednesday'
            when 4 then 'Thursday'  when 5 then 'Friday'   when 6 then 'Saturday'
            when 7 then 'Sunday'
        end                                                   as day_name,
        iso_day_of_week <= 5                                  as is_weekday,
        date_day > current_date                               as is_future
    from days
)

select * from final
