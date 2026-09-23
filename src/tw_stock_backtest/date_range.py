from datetime import date


def parse_date_range(start_text, end_text):
    """解析起訖日期，並確認開始日期不晚於結束日期。"""
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)

    if start > end:
        raise ValueError("開始日期不能晚於結束日期")

    return start, end


def month_starts(start, end):
    """產生日期區間涵蓋的各月份月初。"""
    if start > end:
        raise ValueError("開始日期不能晚於結束日期")

    current = start.replace(day=1)
    last_month = end.replace(day=1)

    while current <= last_month:
        yield current

        if current == last_month:
            break

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def filter_records(records, start, end):
    """只保留起訖日期內的資料，包含起日與迄日。"""
    if start > end:
        raise ValueError("開始日期不能晚於結束日期")

    return [
        record
        for record in records
        if start <= date.fromisoformat(record["date"]) <= end
    ]