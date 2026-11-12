-- Union of every source's account base query (same columns, same order).

select * from {{ ref('base_pn__account') }}
union all
select * from {{ ref('base_iw__account') }}
