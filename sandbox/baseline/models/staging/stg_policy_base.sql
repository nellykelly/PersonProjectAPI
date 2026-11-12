-- Union of every source's policy base query (same columns, same order).

select * from {{ ref('base_pn__policy') }}
union all
select * from {{ ref('base_iw__policy') }}
