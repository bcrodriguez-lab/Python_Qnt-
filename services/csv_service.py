import os
import uuid
import pandas as pd

def save_dataframe_to_csv(df, filename=None):
    """
    Save a pandas DataFrame as CSV to the uploads/generated/ directory.
    
    Args:
        df (pd.DataFrame): The DataFrame to save.
        filename (str, optional): The filename for the CSV. If not provided, 
                                  a unique filename will be generated.
    
    Returns:
        str: The full path to the saved CSV file.
    """
    # Define the target directory
    target_dir = os.path.join("uploads", "generated")
    
    # Create the directory if it doesn't exist
    os.makedirs(target_dir, exist_ok=True)
    
    # Generate a filename if not provided
    if filename is None:
        # Generate a unique filename using UUID
        unique_id = uuid.uuid4()
        filename = f"{unique_id}.csv"
    elif not filename.endswith('.csv'):
        # Append .csv extension if not present
        filename = f"{filename}.csv"
    
    # Construct the full file path
    file_path = os.path.join(target_dir, filename)
    
    # Save the DataFrame to CSV
    df.to_csv(file_path, index=False)
    
    return file_path